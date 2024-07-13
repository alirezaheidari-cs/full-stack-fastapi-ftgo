from typing import Any, Dict, List, Optional
from uuid import uuid4

from config.access_token import ACCESS_TOKEN_TTL_SEC
from config.auth import AUTH_CODE_TTL_SECONDS
from config.cache import RedisConfig
from config.db import PostgresConfig
from config.timezone import tz
from utils.time import utcnow
from models.profile import Profile
from models.address import Address
from data_access.repository.cache_repository import CacheRepository
from data_access.repository.db_repository import DatabaseRepository

from domain.address import AddressDomain
from domain.authentication import Authenticator
from domain.authorization import TokenHandler
from domain.password import PasswordHandler
from domain.uuid_generator import UUIDGenerator

from domain import get_logger
from domain.exceptions import (
    AccountExistsError,
    AddAddressError,
    AddressNotFoundError,
    AuthenticationCodeError,
    DefaultAddressDeletionError,
    DeleteAddressError,
    GetAddressError,
    GetAddressesError,
    GetAddressInfoError,
    InvalidTokenWithUserIdError,
    PasswordHashingError,
    ProfileDeletionError,
    ProfileLoginError,
    ProfileLogoutError,
    ProfileRegistrationError,
    ProfileVerificationError,
    SetDefaultAddressError,
    UnverifiedSessionError,
    UserAlreadyVerifiedError,
    UserNotFoundError,
    UserNotVerifiedError,
    WrongAuthenticationCodeError,
    WrongPasswordError,
)

class UserDomain:
    def __init__(
        self,
        user_id: str,
        first_name: str,
        last_name: str,
        phone_number: str,
        role: str,
        hashed_password: str,
        gender: Optional[str],
        created_at: str,
        updated_at: Optional[str],
        verified_at: Optional[str],
        national_id: Optional[str] = None,
    ):
        self.user_id = user_id
        self.first_name = first_name
        self.last_name = last_name
        self.phone_number = phone_number
        self.hashed_password = hashed_password
        self.gender = gender
        self.created_at = created_at
        self.updated_at = updated_at
        self.verified_at = verified_at
        self.role = role

        self.addresses = None

    @staticmethod
    async def load(user_id: str, access_token: str):
        try:
            user_profile = await DatabaseRepository.fetch_by_query(Profile, query={"id": user_id}, one_or_none=True)
            if not user_profile:
                raise UserNotFoundError(dict(user_id=user_id))

            user = UserDomain._from_profile(user_profile)
            if not user.is_verified():
                raise UserNotVerifiedError(user_id=user_id)

            access_token = await user._get_access_token()
            if not access_token:
                raise UnverifiedSessionError(user_id=user_id)

            if not TokenHandler.validate_token(user.user_id, user._get_secret(), access_token):
                raise InvalidTokenWithUserIdError(user_id=user_id, token=access_token)

            return user
        except Exception as e:
            get_logger().error(str(e), user_id=user_id, access_token=access_token)
            raise e

    @staticmethod
    async def register(
        first_name: str,
        last_name: str,
        phone_number: str,
        password: str,
        role: str,
        national_id: Optional[str] = None,
    ) -> str:
        try:
            current_records = await DatabaseRepository.fetch_by_query(Profile, query={"phone_number": phone_number, "role": role})
            if current_records:
                raise AccountExistsError(phone_number=phone_number, role=role)

            user_id = UUIDGenerator.generate()
            hashed_password = PasswordHandler.hash_password(password)

            new_profile = Profile(
                id=user_id,
                first_name=first_name,
                last_name=last_name,
                phone_number=phone_number,
                hashed_password=hashed_password,
                role=role,
                national_id=national_id,
            )
            new_profile = await DatabaseRepository.insert(new_profile)
            
            user = UserDomain._from_profile(new_profile)
            auth_code = await user._generate_auth_code()

            get_logger().info(f"User with user_id: {user_id} and phone_number: {phone_number} was created successfully")
    
            return user_id, auth_code
    
        except Exception as e:
            get_logger().error(str(e), phone_number=phone_number, role=role)
            raise ProfileRegistrationError() from e
       
    @staticmethod
    async def verify_account(user_id: str, auth_code: str):
        try:
            profile = await DatabaseRepository.fetch_by_query(Profile, query={"id": user_id}, one_or_none=True)
            if not profile:
                raise UserNotFoundError(dict(user_id=user_id))

            user = UserDomain._from_profile(profile)
            if user.is_verified():
                raise UserAlreadyVerifiedError(user_id=user_id)

            if not Authenticator.verify_auth_code(auth_code):
                raise AuthenticationCodeError(user_id=user_id, auth_code=auth_code)
            
            stored_auth_code = await user._get_auth_code()

            if stored_auth_code and stored_auth_code == auth_code:
                verified_profile = (await DatabaseRepository.update_by_query(
                    Profile, query={"id": user_id}, update_fields={"verified_at": utcnow()}
                ))[0]
                user.verified_at = verified_profile.verified_at
                user.updated_at = verified_profile.updated_at
            else:
                raise WrongAuthenticationCodeError(user_id=user_id, auth_code=auth_code, actual_auth_code=stored_auth_code)

            return user_id
        except Exception as e:
            get_logger().error(str(e), user_id=user_id, auth_code=auth_code)
            raise ProfileVerificationError(user_id, auth_code) from e

    @staticmethod
    async def login(phone_number, password, role):
        try:
            profile = await DatabaseRepository.fetch_by_query(Profile, query={"phone_number": phone_number, "role": role}, one_or_none=True)
            if not profile:
                raise UserNotFoundError(dict(phone_number=phone_number, role=role))
            user = UserDomain._from_profile(profile)

            if not user.is_verified():
                raise UserNotVerifiedError(user_id=user.user_id)

        
            if not PasswordHandler.verify_password(password, user.hashed_password):
                raise WrongPasswordError()

            access_token = await user._get_access_token()

            if access_token:
                return user.user_id, access_token

            access_token = await user._generate_session_token()
            return user.user_id, access_token
        except Exception as e:
            get_logger().error(str(e), phone_number=phone_number, role=role)
            raise ProfileLoginError(phone_number=phone_number, role=role) from e

    async def delete_account(self) -> bool:
        try:
            await DatabaseRepository.delete_by_query(Profile, query={"id": self.user_id})
            await CacheRepository.get_cache('token').delete(self.user_id)
            await CacheRepository.get_cache('auth').delete(self.user_id)

        except Exception as e:
            get_logger().error(str(e), user_id=self.user_id)
            raise ProfileDeletionError(user_id=self.user_id) from e

    def get_info(self) -> Dict[str, Any]:
        return dict(
            first_name=self.first_name,
            last_name=self.last_name,
            phone_number=self.phone_number,
            gender=self.gender,
            role=self.role,
        )

    async def logout(self):
        try:
            await CacheRepository.get_cache('token').delete(self.user_id)
        except Exception as e:
            get_logger().error(str(e), user_id=self.user_id)
            raise ProfileLogoutError(user_id=self.user_id) from e

    async def add_address(self, address_line_1: str, address_line_2: str, city: str, postal_code: str = None, country: str = None) -> str:
        try:
            address =  await AddressDomain.add_address(self.user_id, address_line_1, address_line_2, city, postal_code, country)
            if self.addresses is None:
                self.addresses = [address]
            else:
                self.addresses.append(address)
            
            return address.address_id
        except Exception as e:
            get_logger().error(str(e), user_id=self.user_id, address_line_1=address_line_1, address_line_2=address_line_2)
            raise AddAddressError(user_id=self.user_id) from e

    async def get_address_info(self, address_id: str) -> Dict[str, Any]:
        try:
            address = await self.get_address(address_id)
            if not address:
                raise AddressNotFoundError(address_id=address_id)
            
            return address.get_info()
        except Exception as e:
            get_logger().error(str(e), user_id=self.user_id, address_id=address_id)
            raise GetAddressInfoError(user_id=self.user_id, address_id=address_id) from e
    
    async def get_addresses_info(self) -> List[Dict[str, Any]]:
        try:
            if not self.addresses:
                await self.load_addresses()
            
            return [address.get_info() for address in self.addresses]
        except Exception as e:
            get_logger().error(str(e), user_id=self.user_id)
            raise GetAddressesError(user_id=self.user_id) from e

    async def get_address(self, address_id: str) -> Optional[AddressDomain]:
        try:
            if not self.addresses:
                await self.load_addresses()
            
            address = next((address for address in self.addresses if address.address_id == address_id), None)
            if not address:
                return None

            return address
        except Exception as e:
            get_logger().error(str(e), user_id=self.user_id, address_id=address_id)
            raise GetAddressError(user_id=self.user_id, address_id=address_id) from e

    async def delete_address(self, address_id: str) -> bool:
        try:
            address = await self.get_address(address_id)
            if not address:
                raise AddressNotFoundError(address_id=address_id)
            
            if address.is_default:
                raise DefaultAddressDeletionError(address_id=address_id)
            
            await AddressDomain.delete_address(address_id)
            return address_id
        except Exception as e:
            get_logger().error(str(e), user_id=self.user_id, address_id=address_id)
            raise DeleteAddressError(user_id=self.user_id, address_id=address_id) from e

    async def set_address_as_default(self, address_id: str) -> bool:
        try:
            address = await self.get_address(address_id)

            if not address:
                raise AddressNotFoundError(address_id=address_id)
            if address.is_default:
                return True
            
            default_address = next((address for address in self.addresses if address.is_default), None)
            if default_address is not None and default_address.address_id != address_id:
                await default_address.unset_as_default()
            await address.set_as_default()
            return address_id
        except Exception as e:
            get_logger().error(str(e), user_id=self.user_id, address_id=address_id)
            raise SetDefaultAddressError(user_id=self.user_id, address_id=address_id) from e

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "phone_number": self.phone_number,
            "hashed_password": self.hashed_password,
            "nationl_id": self.national_id,
            "gender": self.gender,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "verified_at": self.verified_at,
            "role": self.role,
        }

    def is_verified(self) -> bool:
        return self.verified_at is not None

    async def load_addresses(self) -> List[AddressDomain]:
        if self.addresses is None:
            addresses = await DatabaseRepository.fetch_by_query(Address, query={"user_id": self.user_id})
            self.addresses = [AddressDomain._from_address(address) for address in addresses]
        return self.addresses

    def get_phone_number(self) -> str:
        return self.phone_number

    def get_role(self) -> Optional[str]:
        return self.role

    def _get_secret(self) -> str:
        return f"{self.phone_number}{self.role}"

    async def _get_auth_code(self):
        auth_code = await CacheRepository.get_cache('auth').get(self.user_id)
        if auth_code and Authenticator.verify_auth_code(auth_code):
            return str(auth_code)
        return None

    async def _get_access_token(self):
        access_token = await CacheRepository.get_cache('token').get(self.user_id)
        if access_token and TokenHandler.validate_token(self.user_id, self._get_secret(), access_token):
            return str(access_token)
        return None

    async def _generate_auth_code(self) -> str:
        auth_code, ttl = Authenticator.create_auth_code(self.user_id)
        await CacheRepository.get_cache('auth').set(self.user_id, auth_code, ttl)
        return auth_code

    async def _generate_session_token(self) -> str:
        access_token, ttl = TokenHandler.generate_token(self.user_id, self._get_secret())
        await CacheRepository.get_cache('token').set(self.user_id, access_token, ttl)
        return access_token

    @staticmethod
    def _from_profile(profile: Profile):
        if not profile:
            return None

        return UserDomain(
            user_id=str(profile.id),
            first_name=profile.first_name,
            last_name=profile.last_name,
            phone_number=profile.phone_number,
            hashed_password=profile.hashed_password,
            national_id=profile.national_id,
            gender=profile.gender,
            role=profile.role,
            created_at=profile.created_at.astimezone(tz) if profile.created_at else None,
            updated_at=profile.updated_at.astimezone(tz) if profile.updated_at else None,
            verified_at=profile.verified_at.astimezone(tz) if profile.verified_at else None,
        )
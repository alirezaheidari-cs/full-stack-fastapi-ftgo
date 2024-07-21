import asyncio
from ftgo_utils.logger import get_logger
from rabbitmq_rpc import RPCClient, RabbitMQConfig
from data_access.broker import EventManager
from application import VehicleService, AddressService, ProfileService
from config import BaseConfig, LayerNames, env_var

async def register_events(event_manager: EventManager):
    rpc_client = await event_manager.rpc_client()
    events_handlers = {
        'user.profile.create': ProfileService.register,
        'user.address.add_address': AddressService.add_address,
        'user.driver.vehicle.register_vehicle': VehicleService.register_vehicle,
        'user.profile.verify_account': ProfileService.verify_account,
        'user.profile.login': ProfileService.login,
        'user.profile.get_info': ProfileService.get_info,
        'user.profile.delete_account': ProfileService.delete_account,
        'user.profile.logout': ProfileService.logout,
        'user.profile.update_profile': ProfileService.update_profile,
        'user.profile.get_user_info_with_credentials': ProfileService.get_user_info_with_credentials,
        'user.address.get_default_address': AddressService.get_default_address,
        'user.address.delete_address': AddressService.delete_address,
        'user.address.set_preferred_address': AddressService.set_preferred_address,
        'user.address.get_address_info': AddressService.get_address_info,
        'user.address.get_all_addresses': AddressService.get_all_addresses,
        'user.address.update_address': AddressService.update_address,
        'user.driver.vehicle.get_info': VehicleService.get_vehicle_info,
    }

    for event, handler in events_handlers.items():
        try:
            await rpc_client.register_event(event=event, handler=handler)
            rpc_client.logger.info(f"Registered event '{event}' with handler '{handler.__name__}'")
        except Exception as e:
            rpc_client.logger.error(f"Failed to register event '{event}': {e}")
            raise e

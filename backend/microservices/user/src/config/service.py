import logging

from ftgo_utils import class_property

from config.base import BaseConfig, env_var

class ServiceConfig(BaseConfig):
    @class_property
    def environment(cls):
        return env_var('ENVIRONMENT', 'test')

    @class_property
    def log_level_name(cls):
        return env_var('LOG_LEVEL', 'INFO')
    
    @class_property
    def log_level(cls) -> int:
        return logging._nameToLevel.get(cls.log_level_name, logging.DEBUG)

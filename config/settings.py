from pydantic import Field
from pydantic_settings import BaseSettings
from functools import lru_cache


class SnowflakeSettings(BaseSettings):
    account: str = Field(..., validation_alias="snowflake_account")
    user: str = Field(..., validation_alias="snowflake_user")
    password: str = Field(..., validation_alias="snowflake_password")
    warehouse: str = Field("COMPUTE_WH", validation_alias="snowflake_warehouse")
    database: str = Field("PIPELINEIQ_DB", validation_alias="snowflake_database")
    schema_name: str = Field("RAW", validation_alias="snowflake_schema")
    role: str = Field("SYSADMIN", validation_alias="snowflake_role")

    model_config = {"env_file": ".env", "extra": "ignore"}


class AnthropicSettings(BaseSettings):
    api_key: str = Field(..., validation_alias="anthropic_api_key")
    model: str = Field("claude-sonnet-4-20250514", validation_alias="anthropic_model")
    max_tokens: int = Field(4096, validation_alias="anthropic_max_tokens")

    model_config = {"env_file": ".env", "extra": "ignore"}


class APISettings(BaseSettings):
    secret_key: str = Field("dev_secret_change_me", validation_alias="api_secret_key")
    host: str = Field("0.0.0.0", validation_alias="api_host")
    port: int = Field(8000, validation_alias="api_port")

    model_config = {"env_file": ".env", "extra": "ignore"}


class LangfuseSettings(BaseSettings):
    public_key: str = Field("", validation_alias="langfuse_public_key")
    secret_key: str = Field("", validation_alias="langfuse_secret_key")
    host: str = Field("https://cloud.langfuse.com", validation_alias="langfuse_host")
    enabled: bool = Field(False, validation_alias="langfuse_enabled")

    model_config = {"env_file": ".env", "extra": "ignore"}


class AppSettings(BaseSettings):
    log_level: str = Field("INFO", validation_alias="log_level")
    environment: str = Field("development", validation_alias="environment")

    model_config = {"env_file": ".env", "extra": "ignore"}

    @property
    def snowflake(self) -> SnowflakeSettings:
        return SnowflakeSettings()

    @property
    def anthropic(self) -> AnthropicSettings:
        return AnthropicSettings()

    @property
    def api(self) -> APISettings:
        return APISettings()

    @property
    def langfuse(self) -> LangfuseSettings:
        return LangfuseSettings()


@lru_cache()
def get_settings() -> AppSettings:
    return AppSettings()
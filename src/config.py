from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # IMAP / Neo.com
    imap_host: str = Field("imap.neo.space")
    imap_port: int = Field(993)
    imap_username: str
    imap_password: str

    # AWS S3
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_region: str = Field("us-east-1")
    s3_bucket_name: str
    s3_key_prefix: str = Field("invoices/")

    # App behaviour
    state_file_path: str = Field("./processed_emails.json")
    log_level: str = Field("INFO")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


# Singleton imported by all modules
settings = Settings()

"""UG-DCMS 运行配置。

所有可调参数优先取环境变量; 与系统不变量绑定的参数(如并发许可上限)不在此处,
而是存于数据库 system_setting 表并标记 is_locked, 使其变更留有审计痕迹。
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DCMS_", env_file=".env", extra="ignore")

    # 数据库
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_user: str = "dcms"
    pg_password: str = ""
    pg_database: str = "dcms"
    pg_bin: str = ""
    pg_pool_min: int = 2
    pg_pool_max: int = 20

    # 口令与会话
    # 生产必须保持 12 轮; 仅测试环境可降低以缩短用例时间
    bcrypt_rounds: int = 12
    session_token_bytes: int = 32
    api_prefix: str = "/api/v1"

    # 文件存储 (后续批次使用)
    storage_root: str = "/data/dcms/files"
    backup_root: str = "/data/dcms/backups"
    frontend_root: str = ""

    environment: str = "DEV"

    @property
    def dsn(self) -> str:
        """用 libpq 官方构造器生成连接串, 不要手工拼接。

        手工拼接在口令为空时会出错: libpq 解析 `key=` 后会先跳过空白再取值,
        因此 "password= dbname=X" 会把 "dbname=X" 当成口令值, dbname 随之丢失,
        libpq 再回退到"库名等于用户名"的默认行为, 于是连到一个完全不相干的库。
        socket 认证或 trust 认证下口令本就为空, 这个坑很容易踩到。
        """
        from psycopg.conninfo import make_conninfo
        return make_conninfo(
            host=self.pg_host, port=self.pg_port, user=self.pg_user,
            password=self.pg_password or None, dbname=self.pg_database,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()

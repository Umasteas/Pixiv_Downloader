"""配置管理模块"""
import os
import tempfile
import yaml
from pathlib import Path
from typing import Any, Dict

DEFAULT_CONFIG = {
    "pixiv": {
        "refresh_token": ""
    },
    "download": {
        "output_dir": "./downloads",
        "max_concurrent": 5,
        "retry_times": 3,
        "retry_delays": [1, 3, 5]
    },
    "naming": {
        "template": "{作品ID}_{画师名}_{作品标题}_p{页码}",
        "max_filename_length": 200
    },
    "ugoira": {
        "download_original": True,
        "convert_to_gif": False,
        "convert_to_mp4": True,
        "ffmpeg_path": "auto"
    },
    "proxy": {
        "type": "none",
        "host": "127.0.0.1",
        "port": 1080,
        "username": "",
        "password_encrypted": ""
    },
    "logging": {
        "level": "INFO",
        "max_days": 7
    }
}

class ConfigManager:
    """配置管理器"""
    
    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = Path(config_path)
        self.config = self._load_or_create()
    
    def _load_or_create(self) -> Dict[str, Any]:
        """加载配置文件，不存在则创建默认配置"""
        import copy
        if self.config_path.exists():
            with open(self.config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f) or {}
                # 合并默认值（处理新增配置项）
                return self._merge_with_defaults(config)
        else:
            # 创建默认配置文件
            self._write_config(DEFAULT_CONFIG)
            return copy.deepcopy(DEFAULT_CONFIG)
    
    def _merge_with_defaults(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """合并用户配置与默认配置"""
        import copy
        result = copy.deepcopy(DEFAULT_CONFIG)
        for section, values in config.items():
            if section in result and isinstance(values, dict):
                result[section].update(values)
            else:
                result[section] = values
        return result
    
    def load_config(self) -> Dict[str, Any]:
        """重新加载配置"""
        self.config = self._load_or_create()
        return self.config
    
    def save_config(self) -> None:
        """保存配置到文件"""
        self._write_config(self.config)

    def _write_config(self, config: Dict[str, Any]) -> None:
        """同目录写完整临时文件，再原子替换正式配置。"""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.config_path.parent,
                prefix=f".{self.config_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary_path = Path(stream.name)
                yaml.dump(
                    config,
                    stream,
                    allow_unicode=True,
                    default_flow_style=False,
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.config_path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
    
    def get_setting(self, section: str, key: str, default: Any = None) -> Any:
        """获取单个配置项"""
        return self.config.get(section, {}).get(key, default)
    
    def update_setting(self, section: str, key: str, value: Any) -> None:
        """更新单个配置项"""
        if section not in self.config:
            self.config[section] = {}
        self.config[section][key] = value
        self.save_config()

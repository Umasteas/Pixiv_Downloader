"""配置管理模块"""
import os
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
        "enabled": False,
        "type": "socks5",
        "host": "127.0.0.1",
        "port": 1080,
        "username": "",
        "password": ""
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
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_path, 'w', encoding='utf-8') as f:
                yaml.dump(DEFAULT_CONFIG, f, allow_unicode=True, default_flow_style=False)
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
        with open(self.config_path, 'w', encoding='utf-8') as f:
            yaml.dump(self.config, f, allow_unicode=True, default_flow_style=False)
    
    def get_setting(self, section: str, key: str, default: Any = None) -> Any:
        """获取单个配置项"""
        return self.config.get(section, {}).get(key, default)
    
    def update_setting(self, section: str, key: str, value: Any) -> None:
        """更新单个配置项"""
        if section not in self.config:
            self.config[section] = {}
        self.config[section][key] = value
        self.save_config()

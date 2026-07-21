"""配置管理器测试"""
import pytest
import tempfile
import os
from pathlib import Path
from src.pixiv_downloader.config_manager import ConfigManager, DEFAULT_CONFIG

def test_create_default_config():
    """测试创建默认配置"""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / 'config.yaml'
        cm = ConfigManager(str(config_path))
        
        assert config_path.exists()
        assert cm.config == DEFAULT_CONFIG

def test_load_existing_config():
    """测试加载已存在的配置"""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / 'config.yaml'
        
        # 创建配置
        cm1 = ConfigManager(str(config_path))
        cm1.update_setting('download', 'max_concurrent', 10)
        
        # 重新加载
        cm2 = ConfigManager(str(config_path))
        assert cm2.get_setting('download', 'max_concurrent') == 10

def test_get_setting():
    """测试获取配置项"""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / 'test_get.yaml'
        cm = ConfigManager(str(config_path))
        
        assert cm.get_setting('download', 'max_concurrent') == 5
        assert cm.get_setting('nonexistent', 'key', 'default') == 'default'

def test_update_setting():
    """测试更新配置项"""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / 'config.yaml'
        cm = ConfigManager(str(config_path))
        
        cm.update_setting('download', 'max_concurrent', 20)
        assert cm.get_setting('download', 'max_concurrent') == 20
        
        # 验证持久化
        cm2 = ConfigManager(str(config_path))
        assert cm2.get_setting('download', 'max_concurrent') == 20

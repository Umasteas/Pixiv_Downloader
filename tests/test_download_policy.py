"""Issue #9 下载并发与重试配置契约。"""
import pytest


def test_download_policy_defaults_match_config_contract():
    from src.pixiv_downloader.download_policy import DownloadPolicy

    policy = DownloadPolicy.from_mapping({})

    assert policy.max_concurrent == 5
    assert policy.retry_times == 3
    assert policy.retry_delays == (1.0, 3.0, 5.0)


def test_download_policy_aligns_delay_count_to_retry_times():
    from src.pixiv_downloader.download_policy import DownloadPolicy

    assert DownloadPolicy.from_mapping(
        {"max_concurrent": 2, "retry_times": 4, "retry_delays": [0.25, 1]}
    ).retry_delays == (0.25, 1.0, 1.0, 1.0)
    assert DownloadPolicy.from_mapping(
        {"retry_times": 2, "retry_delays": [1, 3, 5]}
    ).retry_delays == (1.0, 3.0)
    assert DownloadPolicy.from_mapping(
        {"retry_times": 0, "retry_delays": [1, 3, 5]}
    ).retry_delays == ()


@pytest.mark.parametrize(
    "mapping",
    [
        {"max_concurrent": 0},
        {"max_concurrent": True},
        {"max_concurrent": "5"},
        {"retry_times": -1},
        {"retry_times": 1.5},
        {"retry_times": True},
        {"retry_times": 1, "retry_delays": []},
        {"retry_times": 1, "retry_delays": [-1]},
        {"retry_times": 1, "retry_delays": [True]},
        {"retry_times": 1, "retry_delays": ["1"]},
        {"retry_times": 1, "retry_delays": None},
    ],
)
def test_download_policy_rejects_invalid_runtime_configuration(mapping):
    from src.pixiv_downloader.download_policy import DownloadPolicy

    with pytest.raises(ValueError):
        DownloadPolicy.from_mapping(mapping)

// Pixiv Downloader Frontend
console.log('Pixiv Downloader initialized');

// Socket.IO 连接
const socket = io();

socket.on('connect', () => {
    console.log('Connected to server');
});

socket.on('disconnect', () => {
    console.log('Disconnected from server');
});

const tokenForm = document.getElementById('token-form');

if (tokenForm) {
    tokenForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const tokenInput = document.getElementById('refresh-token');
        const submitButton = document.getElementById('token-submit');
        const feedback = document.getElementById('token-feedback');

        submitButton.disabled = true;
        feedback.className = 'alert alert-info mt-3';
        feedback.textContent = '正在验证 Token…';

        try {
            const response = await fetch('/api/auth/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ refresh_token: tokenInput.value.trim() }),
            });
            const result = await response.json();

            if (!response.ok) {
                throw new Error(result.error || 'Token 验证失败，请检查后重试。');
            }

            tokenInput.value = '';
            feedback.className = 'alert alert-success mt-3';
            feedback.textContent = result.message;
        } catch (error) {
            feedback.className = 'alert alert-danger mt-3';
            feedback.textContent = error.message;
        } finally {
            submitButton.disabled = false;
        }
    });
}

const proxyForm = document.getElementById('proxy-form');
const proxyTest = document.getElementById('proxy-test');

if (proxyForm && proxyTest) {
    const feedback = document.getElementById('proxy-feedback');
    const details = document.getElementById('proxy-details');
    const host = document.getElementById('proxy-host');
    const port = document.getElementById('proxy-port');
    const username = document.getElementById('proxy-username');
    const password = document.getElementById('proxy-password');
    const clearPassword = document.getElementById('proxy-clear-password');
    let savedPasswordPresent = false;

    const selectedType = () => proxyForm.querySelector('input[name="proxy-type"]:checked').value;

    const showProxyFeedback = (kind, message) => {
        feedback.className = `alert alert-${kind} mt-3`;
        feedback.textContent = message;
    };

    const updateProxyDetails = () => {
        const needsDetails = ['http', 'socks5'].includes(selectedType());
        details.querySelectorAll('input').forEach((input) => {
            input.disabled = !needsDetails;
        });
        details.classList.toggle('opacity-50', !needsDetails);
    };

    const proxyPayload = () => {
        const type = selectedType();
        if (['none', 'system'].includes(type)) {
            return { type, clear_password: true };
        }
        return {
            type,
            host: host.value.trim(),
            port: port.value,
            username: username.value,
            password: password.value || (savedPasswordPresent ? '***' : ''),
            clear_password: clearPassword.checked,
        };
    };

    const saveProxy = async () => {
        const response = await fetch('/api/proxy', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(proxyPayload()),
        });
        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.error || '代理配置保存失败。');
        }
        savedPasswordPresent = result.proxy.password === '***';
        password.value = '';
        clearPassword.checked = false;
        return result;
    };

    proxyForm.querySelectorAll('input[name="proxy-type"]').forEach((radio) => {
        radio.addEventListener('change', updateProxyDetails);
    });

    proxyForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        try {
            await saveProxy();
            showProxyFeedback('success', '代理配置已保存。');
        } catch (error) {
            showProxyFeedback('danger', error.message);
        }
    });

    proxyTest.addEventListener('click', async () => {
        proxyTest.disabled = true;
        showProxyFeedback('info', '正在测试 Pixiv 连接…');
        try {
            const response = await fetch('/api/proxy/test', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(proxyPayload()),
            });
            const result = await response.json();
            if (!response.ok) {
                throw new Error(result.message || '代理连接测试失败。');
            }
            showProxyFeedback('success', result.message);
        } catch (error) {
            showProxyFeedback('danger', error.message);
        } finally {
            proxyTest.disabled = false;
        }
    });

    fetch('/api/proxy')
        .then((response) => response.json())
        .then(({ proxy }) => {
            const radio = proxyForm.querySelector(`input[name="proxy-type"][value="${proxy.type}"]`);
            if (radio) radio.checked = true;
            host.value = proxy.host || '';
            port.value = proxy.port || '';
            username.value = proxy.username || '';
            savedPasswordPresent = proxy.password === '***';
            clearPassword.checked = false;
            updateProxyDetails();
        })
        .catch((error) => showProxyFeedback('danger', `代理配置加载失败：${error.message}`));
}

const artistDownloadForm = document.getElementById('artist-download-form');

if (artistDownloadForm) {
    const artistIdInput = document.getElementById('artist-id');
    const submitButton = document.getElementById('artist-download-submit');
    const feedback = document.getElementById('artist-download-feedback');

    artistDownloadForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        submitButton.disabled = true;
        feedback.className = 'alert alert-info mt-3';
        feedback.textContent = '正在获取画师作品并下载，请稍候…';

        try {
            const response = await fetch('/api/download/artist', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ artist_id: Number(artistIdInput.value) }),
            });
            const payload = await response.json();
            if (!response.ok) {
                throw new Error(payload.error || '画师作品下载失败。');
            }
            const result = payload.result;
            feedback.className = `alert alert-${result.failed ? 'warning' : 'success'} mt-3`;
            feedback.textContent = `处理 ${result.total} 个作品：下载 ${result.downloaded}，跳过 ${result.skipped}，失败 ${result.failed}。`;
        } catch (error) {
            feedback.className = 'alert alert-danger mt-3';
            feedback.textContent = error.message;
        } finally {
            submitButton.disabled = false;
        }
    });
}

const followCheckButton = document.getElementById('follow-check-button');
const followArtworkGroups = document.getElementById('follow-artwork-groups');

if (followCheckButton && followArtworkGroups) {
    const lastCheck = document.getElementById('follow-last-check');
    const newCount = document.getElementById('follow-new-count');
    const bookmarkFilter = document.getElementById('follow-bookmark-filter');
    const selectAll = document.getElementById('follow-select-all');
    const clearAll = document.getElementById('follow-clear-all');
    const downloadSelected = document.getElementById('follow-download-selected');
    const feedback = document.getElementById('follow-feedback');

    const showFollowFeedback = (kind, message) => {
        feedback.className = `alert alert-${kind} mt-3`;
        feedback.textContent = message;
    };
    const applyBookmarkFilter = () => {
        const minimum = Math.max(0, Number(bookmarkFilter.value) || 0);
        followArtworkGroups.querySelectorAll('[data-follow-artwork]').forEach((row) => {
            const visible = Number(row.dataset.bookmarks) >= minimum;
            row.classList.toggle('d-none', !visible);
            if (!visible) row.querySelector('input[type="checkbox"]').checked = false;
        });
    };
    const renderFollowResult = (result) => {
        followArtworkGroups.replaceChildren();
        lastCheck.textContent = result.checked_at || '未知';
        newCount.textContent = String(result.new_count || 0);
        result.artists.forEach((artist) => {
            const section = document.createElement('section');
            section.className = 'border rounded p-3 mb-3';
            const title = document.createElement('h3');
            title.className = 'h5';
            title.textContent = `${artist.artist_name}（${artist.new_count} 个新作品）`;
            section.appendChild(title);
            const previous = document.createElement('p');
            previous.className = 'small text-muted';
            previous.textContent = `此前检查：${artist.last_check_time || '首次检查'}`;
            section.appendChild(previous);
            artist.artworks.forEach((artwork) => {
                const row = document.createElement('label');
                row.className = 'd-flex gap-3 align-items-center border-top py-2';
                row.dataset.followArtwork = String(artwork.artwork_id);
                row.dataset.bookmarks = String(artwork.bookmarks || 0);
                const checkbox = document.createElement('input');
                checkbox.className = 'form-check-input';
                checkbox.type = 'checkbox';
                checkbox.checked = true;
                checkbox.value = String(artwork.artwork_id);
                checkbox.setAttribute('aria-label', `选择作品 ${artwork.artwork_id}`);
                const image = document.createElement('img');
                image.src = artwork.thumbnail_url;
                image.alt = artwork.title;
                image.width = 72;
                image.height = 72;
                image.loading = 'lazy';
                image.referrerPolicy = 'no-referrer';
                image.className = 'rounded object-fit-cover';
                const details = document.createElement('span');
                const artworkTitle = document.createElement('strong');
                artworkTitle.textContent = artwork.title;
                const bookmarks = document.createElement('small');
                bookmarks.className = 'd-block text-muted';
                bookmarks.textContent = `收藏 ${artwork.bookmarks || 0}`;
                details.append(artworkTitle, bookmarks);
                row.append(checkbox, image, details);
                section.appendChild(row);
            });
            followArtworkGroups.appendChild(section);
        });
        applyBookmarkFilter();
    };

    followCheckButton.addEventListener('click', async () => {
        followCheckButton.disabled = true;
        showFollowFeedback('info', '正在检查关注画师的新作品…');
        try {
            const response = await fetch('/api/follow/latest', { method: 'POST' });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.error || '关注新作检查失败。');
            renderFollowResult(payload.result);
            showFollowFeedback('success', `检查完成，发现 ${payload.result.new_count} 个新作品。`);
        } catch (error) {
            showFollowFeedback('danger', error.message);
        } finally {
            followCheckButton.disabled = false;
        }
    });
    bookmarkFilter.addEventListener('input', applyBookmarkFilter);
    selectAll.addEventListener('click', () => {
        followArtworkGroups.querySelectorAll('[data-follow-artwork]:not(.d-none) input[type="checkbox"]').forEach((input) => {
            input.checked = true;
        });
    });
    clearAll.addEventListener('click', () => {
        followArtworkGroups.querySelectorAll('input[type="checkbox"]').forEach((input) => {
            input.checked = false;
        });
    });
    downloadSelected.addEventListener('click', async () => {
        const artworkIds = Array.from(followArtworkGroups.querySelectorAll('input[type="checkbox"]:checked'))
            .map((input) => Number(input.value));
        if (!artworkIds.length) {
            showFollowFeedback('warning', '请至少选择一个作品。');
            return;
        }
        downloadSelected.disabled = true;
        try {
            const response = await fetch('/api/follow/download', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ artwork_ids: artworkIds }),
            });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.error || '创建下载任务失败。');
            showFollowFeedback('success', `已创建任务 #${payload.task_id}，包含 ${payload.selected_count} 个作品。`);
        } catch (error) {
            showFollowFeedback('danger', error.message);
        } finally {
            downloadSelected.disabled = false;
        }
    });
}

const statsRange = document.getElementById('stats-range');

if (statsRange) {
    const feedback = document.getElementById('stats-feedback');
    const charts = {};
    const colors = ['#0d6efd', '#6f42c1', '#d63384', '#dc3545', '#fd7e14', '#198754', '#20c997', '#0dcaf0', '#6c757d', '#ffc107'];
    const taskTypeLabels = {
        artist: '画师',
        search: '搜索',
        ranking: '排行榜',
        bookmarks: '收藏夹',
        follow_latest: '关注新作',
    };
    const formatFileSize = (bytes) => {
        if (!bytes) return '0 B';
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
        return `${(bytes / (1024 ** exponent)).toFixed(exponent ? 1 : 0)} ${units[exponent]}`;
    };
    const renderChart = (name, canvasId, type, labels, values, label) => {
        if (charts[name]) charts[name].destroy();
        charts[name] = new Chart(document.getElementById(canvasId), {
            type,
            data: {
                labels,
                datasets: [{
                    label,
                    data: values,
                    backgroundColor: type === 'line' ? 'rgba(13, 110, 253, 0.18)' : colors,
                    borderColor: type === 'line' ? '#0d6efd' : colors,
                    borderWidth: 1,
                    fill: type === 'line',
                    tension: 0.25,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: type === 'pie' } },
                scales: type === 'pie' ? {} : { y: { beginAtZero: true, ticks: { precision: 0 } } },
            },
        });
    };
    const renderStats = (stats) => {
        document.getElementById('stats-total-downloads').textContent = String(stats.totals.downloads);
        document.getElementById('stats-total-artists').textContent = String(stats.totals.artists);
        document.getElementById('stats-total-size').textContent = formatFileSize(stats.totals.file_size);
        renderChart('artists', 'stats-artists-chart', 'bar', stats.top_artists.map((item) => item.artist_name || String(item.artist_id)), stats.top_artists.map((item) => item.count), '下载数');
        renderChart('tags', 'stats-tags-chart', 'bar', stats.top_tags.map((item) => item.tag), stats.top_tags.map((item) => item.count), '使用数');
        renderChart('trend', 'stats-trend-chart', 'line', stats.trend.map((item) => item.date), stats.trend.map((item) => item.count), '下载数');
        renderChart('types', 'stats-types-chart', 'pie', stats.task_types.map((item) => taskTypeLabels[item.task_type] || item.task_type), stats.task_types.map((item) => item.count), '任务数');
    };
    const loadStats = async () => {
        feedback.className = 'alert alert-info mt-3';
        feedback.textContent = '正在加载统计数据…';
        try {
            const response = await fetch(`/api/stats?range=${encodeURIComponent(statsRange.value)}`);
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.error || '统计数据加载失败。');
            if (typeof Chart === 'undefined') throw new Error('图表组件加载失败，请检查网络连接。');
            renderStats(payload.stats);
            feedback.className = 'alert alert-success mt-3 d-none';
            feedback.textContent = '';
        } catch (error) {
            feedback.className = 'alert alert-danger mt-3';
            feedback.textContent = error.message;
        }
    };

    statsRange.addEventListener('change', loadStats);
    loadStats();
}

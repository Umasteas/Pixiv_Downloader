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

const ugoiraForm = document.getElementById('ugoira-form');

if (ugoiraForm) {
    const original = document.getElementById('ugoira-download-original');
    const gif = document.getElementById('ugoira-convert-gif');
    const mp4 = document.getElementById('ugoira-convert-mp4');
    const save = document.getElementById('ugoira-save');
    const feedback = document.getElementById('ugoira-feedback');
    const ffmpegStatus = document.getElementById('ugoira-ffmpeg-status');
    const ffmpegDownload = document.getElementById('ugoira-ffmpeg-download');
    const payload = () => ({
        download_original: original.checked,
        convert_to_gif: gif.checked,
        convert_to_mp4: mp4.checked,
    });
    const renderSettings = (result) => {
        original.checked = result.ugoira.download_original;
        gif.checked = result.ugoira.convert_to_gif;
        mp4.checked = result.ugoira.convert_to_mp4;
        ffmpegStatus.className = `alert mt-3 alert-${result.ffmpeg.available ? 'success' : 'warning'}`;
        ffmpegStatus.textContent = result.ffmpeg.message;
        ffmpegDownload.href = result.ffmpeg.download_url;
        ffmpegDownload.classList.toggle('d-none', result.ffmpeg.available);
    };
    const showFeedback = (kind, message) => {
        feedback.className = `alert alert-${kind} mt-3`;
        feedback.textContent = message;
    };

    ugoiraForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const values = payload();
        if (!Object.values(values).some(Boolean)) {
            showFeedback('danger', '请至少选择一种动图保存格式。');
            return;
        }
        save.disabled = true;
        try {
            const response = await fetch('/api/ugoira', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(values),
            });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || '动图设置保存失败。');
            renderSettings(result);
            showFeedback('success', '动图设置已保存。');
        } catch (error) {
            showFeedback('danger', error.message);
        } finally {
            save.disabled = false;
        }
    });

    fetch('/api/ugoira')
        .then(async (response) => {
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || '动图设置加载失败。');
            return result;
        })
        .then(renderSettings)
        .catch((error) => showFeedback('danger', error.message));
}

const taskList = document.getElementById('task-list');
const taskListFeedback = document.getElementById('task-list-feedback');
const liveTaskProgress = new Map();
let renderedTasks = [];
const taskStatusLabels = {
    pending: '等待中', running: '运行中', paused: '已暂停', completed: '已完成',
    cancelled: '已取消', failed: '失败',
};
const taskLogConsole = document.getElementById('task-log-console');
const taskLogClear = document.getElementById('task-log-clear');

socket.on('log_entry', (entry) => {
    if (!taskLogConsole) return;
    const row = document.createElement('li');
    row.className = `border-top py-1 text-${entry.level === 'ERROR' ? 'danger' : entry.level === 'WARNING' ? 'warning' : 'body'}`;
    row.textContent = `${entry.timestamp || ''} [${entry.level || 'INFO'}] ${entry.message || ''}`;
    taskLogConsole.append(row);
    while (taskLogConsole.children.length > 200) taskLogConsole.firstElementChild.remove();
    taskLogConsole.scrollTop = taskLogConsole.scrollHeight;
});
if (taskLogClear) taskLogClear.addEventListener('click', () => taskLogConsole.replaceChildren());
const taskActions = {
    pending: [['start', '开始'], ['cancel', '取消']],
    running: [['pause', '暂停'], ['cancel', '取消']],
    paused: [['resume', '恢复'], ['cancel', '取消']],
};
const taskDetailCache = new Map();

const renderTaskDetail = (panel, payload) => {
    panel.replaceChildren();
    const counts = document.createElement('p');
    counts.className = 'mb-2';
    counts.textContent = `成功 ${payload.task.downloaded_count} · 跳过 ${payload.task.skipped_count} · 失败 ${payload.task.failed_count}`;
    panel.append(counts);
    const list = document.createElement('ul');
    list.className = 'mb-0';
    payload.failed_artworks.forEach((failure) => {
        const item = document.createElement('li');
        item.textContent = `作品 ${failure.artwork_id} · ${failure.message}`;
        list.append(item);
    });
    if (payload.failed_artworks.length) {
        panel.append(list);
    } else {
        const empty = document.createElement('p');
        empty.className = 'text-muted mb-0';
        empty.textContent = '没有失败作品。';
        panel.append(empty);
    }
    panel.classList.remove('d-none');
};
const formatEta = (seconds) => {
    if (seconds === null || seconds === undefined) return '待计算';
    const rounded = Math.max(0, Math.ceil(seconds));
    return rounded < 60 ? `${rounded} 秒` : `${Math.floor(rounded / 60)} 分 ${rounded % 60} 秒`;
};
const filterLabels = {
    illust: '插画', manga: '漫画', ugoira: '动图',
    general: '全年龄', r18: 'R-18', r18g: 'R-18-G',
};
const formatFilterSummary = (filters) => {
    if (!filters) return '';
    const types = (filters.work_types || []).map((value) => filterLabels[value] || value).join('/');
    const ratings = (filters.r18_modes || []).map((value) => filterLabels[value] || value).join('/');
    return `筛选：${types || '全部类型'} · ${ratings || '全部分级'} · 收藏 ≥ ${Number(filters.min_bookmarks || 0)}`;
};

const renderTasks = (tasks) => {
    renderedTasks = tasks;
    taskList.replaceChildren();
    if (!tasks.length) {
        const empty = document.createElement('p');
        empty.className = 'text-muted mb-0';
        empty.textContent = '暂无下载任务。';
        taskList.append(empty);
        return;
    }
    tasks.forEach((storedTask) => {
        const task = { ...storedTask, ...(liveTaskProgress.get(storedTask.task_id) || {}) };
        const processed = task.downloaded_count + task.skipped_count + task.failed_count;
        const total = task.total_count ?? '待获取';
        const percent = task.total_count ? Math.min(100, Math.round(processed * 100 / task.total_count)) : 0;
        const card = document.createElement('div');
        card.className = 'border rounded p-3';
        card.dataset.taskCard = task.task_id;
        const header = document.createElement('div');
        header.className = 'd-flex flex-wrap justify-content-between gap-3';
        const summary = document.createElement('div');
        const artistId = task.query?.artist_id;
        const filterSummary = formatFilterSummary(task.filters);
        let taskLabel = task.task_type;
        if (artistId) taskLabel = `画师 ${artistId}`;
        if (task.task_type === 'search') taskLabel = `搜索 ${task.query?.keyword}`;
        if (task.task_type === 'retry_failed') {
            taskLabel = `失败项重试（来源任务 #${task.query?.source_task_id}）`;
        }
        summary.textContent = `#${task.task_id} · ${taskLabel} · ${taskStatusLabels[task.status] || task.status}${filterSummary ? ` · ${filterSummary}` : ''}`;
        const controls = document.createElement('div');
        controls.className = 'd-flex gap-2';
        (taskActions[task.status] || []).forEach(([action, label]) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = `btn btn-sm ${action === 'cancel' ? 'btn-outline-danger' : 'btn-outline-primary'}`;
            button.dataset.taskId = task.task_id;
            button.dataset.taskAction = action;
            button.textContent = label;
            controls.append(button);
        });
        const detailButton = document.createElement('button');
        detailButton.type = 'button';
        detailButton.className = 'btn btn-sm btn-outline-secondary';
        detailButton.dataset.taskDetail = task.task_id;
        detailButton.textContent = '详情';
        controls.append(detailButton);
        if (task.failed_count && ['completed', 'failed'].includes(task.status)) {
            const retryButton = document.createElement('button');
            retryButton.type = 'button';
            retryButton.className = 'btn btn-sm btn-outline-warning';
            retryButton.dataset.retryFailed = task.task_id;
            retryButton.textContent = '重试失败项';
            controls.append(retryButton);
        }
        header.append(summary, controls);
        const progress = document.createElement('div');
        progress.className = 'progress mt-3';
        progress.setAttribute('role', 'progressbar');
        progress.setAttribute('aria-label', `任务 ${task.task_id} 下载进度`);
        progress.setAttribute('aria-valuemin', '0');
        progress.setAttribute('aria-valuemax', '100');
        progress.setAttribute('aria-valuenow', String(percent));
        const bar = document.createElement('div');
        bar.className = 'progress-bar';
        bar.style.width = `${percent}%`;
        bar.textContent = `${percent}%`;
        progress.append(bar);
        const metrics = document.createElement('div');
        metrics.className = 'small text-muted mt-2';
        metrics.textContent = `进度 ${processed}/${total} · 下载 ${task.downloaded_count} / 跳过 ${task.skipped_count} / 失败 ${task.failed_count} · 速度 ${Number(task.current_speed || 0).toFixed(2)} MB/s · 剩余 ${formatEta(task.eta_seconds)}`;
        const files = document.createElement('ul');
        files.className = 'list-unstyled small mb-0 mt-2';
        (task.current_files || []).slice(0, 5).forEach((file) => {
            const item = document.createElement('li');
            item.textContent = `${file.filename} · ${file.progress}%`;
            files.append(item);
        });
        const detailPanel = document.createElement('div');
        detailPanel.className = 'mt-3 d-none';
        detailPanel.dataset.taskDetailPanel = task.task_id;
        if (taskDetailCache.has(task.task_id)) {
            renderTaskDetail(detailPanel, taskDetailCache.get(task.task_id));
        }
        card.append(header, progress, metrics, files, detailPanel);
        taskList.append(card);
    });
};

const loadTasks = async () => {
    if (!taskList) return;
    try {
        const response = await fetch('/api/tasks');
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || '任务列表加载失败。');
        renderTasks(payload.tasks);
        taskListFeedback.className = 'alert mt-3 d-none';
    } catch (error) {
        taskListFeedback.className = 'alert alert-danger mt-3';
        taskListFeedback.textContent = error.message;
    }
};

const applyLiveTaskEvent = (payload) => {
    liveTaskProgress.set(payload.task_id, {
        ...(liveTaskProgress.get(payload.task_id) || {}),
        ...payload,
    });
    if (taskList && renderedTasks.some((task) => task.task_id === payload.task_id)) {
        renderTasks(renderedTasks);
    } else {
        loadTasks();
    }
};

socket.on('progress_update', applyLiveTaskEvent);
socket.on('task_complete', (payload) => {
    applyLiveTaskEvent({ ...payload, current_files: [], current_speed: 0, eta_seconds: 0 });
    loadTasks();
});
socket.on('error', (payload) => {
    applyLiveTaskEvent({ ...payload, current_files: [], current_speed: 0 });
    if (taskListFeedback) {
        taskListFeedback.className = 'alert alert-danger mt-3';
        taskListFeedback.textContent = payload.message || '任务执行失败。';
    }
    loadTasks();
});

if (taskList) {
    document.getElementById('task-refresh').addEventListener('click', loadTasks);
    taskList.addEventListener('click', async (event) => {
        const detailButton = event.target.closest('[data-task-detail]');
        if (detailButton) {
            const panel = taskList.querySelector(`[data-task-detail-panel="${detailButton.dataset.taskDetail}"]`);
            detailButton.disabled = true;
            try {
                const response = await fetch(`/api/tasks/${detailButton.dataset.taskDetail}`);
                const payload = await response.json();
                if (!response.ok) throw new Error(payload.error || '任务详情加载失败。');
                taskDetailCache.set(Number(detailButton.dataset.taskDetail), payload);
                renderTaskDetail(panel, payload);
            } catch (error) {
                taskListFeedback.className = 'alert alert-danger mt-3';
                taskListFeedback.textContent = error.message;
            } finally {
                detailButton.disabled = false;
            }
            return;
        }
        const retryButton = event.target.closest('[data-retry-failed]');
        if (retryButton) {
            retryButton.disabled = true;
            try {
                const response = await fetch(`/api/tasks/${retryButton.dataset.retryFailed}/retry-failed`, { method: 'POST' });
                const payload = await response.json();
                if (!response.ok) throw new Error(payload.error || '失败作品重试任务创建失败。');
                taskListFeedback.className = 'alert alert-success mt-3';
                taskListFeedback.textContent = `已创建失败项重试任务 #${payload.task_id}。`;
                await loadTasks();
            } catch (error) {
                taskListFeedback.className = 'alert alert-danger mt-3';
                taskListFeedback.textContent = error.message;
            } finally {
                retryButton.disabled = false;
            }
            return;
        }
        const button = event.target.closest('[data-task-action]');
        if (!button) return;
        button.disabled = true;
        try {
            const response = await fetch(`/api/tasks/${button.dataset.taskId}/${button.dataset.taskAction}`, { method: 'POST' });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.error || '任务操作失败。');
            await loadTasks();
        } catch (error) {
            taskListFeedback.className = 'alert alert-danger mt-3';
            taskListFeedback.textContent = error.message;
        } finally {
            button.disabled = false;
        }
    });
    loadTasks();
    window.setInterval(loadTasks, 1000);
}

const artistDownloadForm = document.getElementById('artist-download-form');

if (artistDownloadForm) {
    const artistIdInput = document.getElementById('artist-id');
    const minBookmarksInput = document.getElementById('min-bookmarks');
    const submitButton = document.getElementById('artist-download-submit');
    const feedback = document.getElementById('artist-download-feedback');
    const checkedValues = (name) => Array.from(
        artistDownloadForm.querySelectorAll(`input[name="${name}"]:checked`),
        (input) => input.value,
    );

    artistDownloadForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const workTypes = checkedValues('work-types');
        const r18Modes = checkedValues('r18-modes');
        if (!workTypes.length || !r18Modes.length) {
            feedback.className = 'alert alert-danger mt-3';
            feedback.textContent = '请至少选择一种作品类型和一个分级。';
            return;
        }
        submitButton.disabled = true;
        feedback.className = 'alert alert-info mt-3';
        feedback.textContent = '正在创建下载任务…';

        try {
            const response = await fetch('/api/tasks', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    task_type: 'artist',
                    query: { artist_id: Number(artistIdInput.value) },
                    filters: {
                        work_types: workTypes,
                        r18_modes: r18Modes,
                        min_bookmarks: Number(minBookmarksInput.value) || 0,
                    },
                }),
            });
            const payload = await response.json();
            if (!response.ok) {
                throw new Error(payload.error || '画师任务创建失败。');
            }
            feedback.className = 'alert alert-success mt-3';
            feedback.textContent = `已创建等待中的任务 #${payload.task_id}，请在任务列表中开始。`;
            await loadTasks();
        } catch (error) {
            feedback.className = 'alert alert-danger mt-3';
            feedback.textContent = error.message;
        } finally {
            submitButton.disabled = false;
        }
    });
}

const searchDownloadForm = document.getElementById('search-download-form');

if (searchDownloadForm) {
    const keywordInput = document.getElementById('search-keyword');
    const searchTypeInput = document.getElementById('search-type');
    const sortInput = document.getElementById('search-sort');
    const limitInput = document.getElementById('search-limit');
    const minBookmarksInput = document.getElementById('search-min-bookmarks');
    const submitButton = document.getElementById('search-download-submit');
    const feedback = document.getElementById('search-download-feedback');
    const checkedValues = (name) => Array.from(
        searchDownloadForm.querySelectorAll(`input[name="${name}"]:checked`),
        (input) => input.value,
    );

    searchDownloadForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const keyword = keywordInput.value.trim();
        const limit = Number(limitInput.value);
        const workTypes = checkedValues('search-work-types');
        const r18Modes = checkedValues('search-r18-modes');
        if (!keyword || !Number.isInteger(limit) || limit < 1 || limit > 10000) {
            feedback.className = 'alert alert-danger mt-3';
            feedback.textContent = '请输入关键词，并将数量设为 1 至 10000 的整数。';
            return;
        }
        if (!workTypes.length || !r18Modes.length) {
            feedback.className = 'alert alert-danger mt-3';
            feedback.textContent = '请至少选择一种作品类型和一个分级。';
            return;
        }
        submitButton.disabled = true;
        feedback.className = 'alert alert-info mt-3';
        feedback.textContent = '正在创建搜索任务…';
        try {
            const response = await fetch('/api/tasks', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    task_type: 'search',
                    query: {
                        keyword,
                        search_type: searchTypeInput.value,
                        sort: sortInput.value,
                        limit,
                    },
                    filters: {
                        work_types: workTypes,
                        r18_modes: r18Modes,
                        min_bookmarks: Number(minBookmarksInput.value) || 0,
                    },
                }),
            });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.error || '搜索任务创建失败。');
            feedback.className = 'alert alert-success mt-3';
            feedback.textContent = `已创建搜索任务 #${payload.task_id}，请在任务列表中开始。`;
            await loadTasks();
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
                const pages = document.createElement('small');
                pages.className = 'd-block text-muted';
                pages.textContent = artwork.page_count > 1 ? `共 ${artwork.page_count} 页` : '';
                details.append(artworkTitle, pages, bookmarks);
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

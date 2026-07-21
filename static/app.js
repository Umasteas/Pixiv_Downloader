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

    const proxyPayload = () => ({
        type: selectedType(),
        host: host.value.trim(),
        port: port.value,
        username: username.value,
        password: password.value || (savedPasswordPresent ? '***' : ''),
    });

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
            await saveProxy();
            const response = await fetch('/api/proxy/test', { method: 'POST' });
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
            updateProxyDetails();
        })
        .catch((error) => showProxyFeedback('danger', `代理配置加载失败：${error.message}`));
}

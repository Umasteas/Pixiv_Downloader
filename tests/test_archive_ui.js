'use strict';

const assert = require('node:assert/strict');
const ArchiveUI = require('../static/archive_ui.js');

(async () => {
    const clicks = [];
    const revoked = [];
    const scheduled = [];
    const links = [];
    const documentRef = {
        body: {
            append(link) { links.push(link); },
        },
        createElement(tag) {
            assert.equal(tag, 'a');
            return {
                click() { clicks.push(this.download); },
                remove() {},
            };
        },
    };
    const urlApi = {
        createObjectURL(blob) {
            assert.equal(blob, 'zip-blob');
            return 'blob:archive';
        },
        revokeObjectURL(url) { revoked.push(url); },
    };
    const ui = ArchiveUI({
        documentRef,
        urlApi,
        scheduler(callback, delay) { scheduled.push({ callback, delay }); },
        async fetchImpl(url, options) {
            assert.equal(url, '/archive');
            assert.equal(options.headers['X-App-Token'], 'token');
            return { ok: true, blob: async () => 'zip-blob' };
        },
    });

    ui.setDeleteSelection(7, true);
    ui.setProgress({ task_id: 7, current: 2, total: 5 });
    ui.rememberTasks([
        { task_id: 7, archive: { filename: 'task.zip', download_url: '/archive' } },
        { task_id: 8 },
    ]);
    assert.deepEqual(ui.progress.get(7), { task_id: 7, current: 2, total: 5 });
    assert.equal(ui.deleteSelections.get(7), true);
    assert.equal(ui.archives.get(7).filename, 'task.zip');
    assert.equal(ui.archives.has(8), false);

    ui.rememberTasks([{ task_id: 7 }]);
    assert.equal(ui.archives.has(7), false);
    assert.equal(ui.deleteSelections.get(7), true);

    await ui.download({ filename: 'task.zip', download_url: '/archive' }, 'token');
    assert.deepEqual(clicks, ['task.zip']);
    assert.equal(links.length, 1);
    assert.equal(scheduled.length, 1);
    assert.equal(scheduled[0].delay, 1000);
    scheduled[0].callback();
    assert.deepEqual(revoked, ['blob:archive']);

    console.log('archive_ui behavior tests passed');
})().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});

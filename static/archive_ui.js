(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory;
  else root.ArchiveUI = factory;
}(typeof globalThis !== 'undefined' ? globalThis : this, function createArchiveUI(options) {
  const opts = options || {};
  const documentRef = opts.documentRef || (typeof document !== 'undefined' ? document : null);
  const urlApi = opts.urlApi || (typeof URL !== 'undefined' ? URL : null);
  const scheduler = opts.scheduler || ((fn, ms) => setTimeout(fn, ms));
  const fetchImpl = opts.fetchImpl || (typeof fetch !== 'undefined' ? fetch : null);
  const progress = new Map();
  const archives = new Map();
  const deleteSelections = new Map();

  function rememberTasks(tasks) {
    (tasks || []).forEach((task) => {
      if (task.archive) archives.set(task.task_id, task.archive);
      else archives.delete(task.task_id);
    });
  }
  function setProgress(payload) { progress.set(payload.task_id, payload); }
  function clearProgress(taskId) { progress.delete(taskId); }
  function setDeleteSelection(taskId, checked) { deleteSelections.set(Number(taskId), Boolean(checked)); }
  function getDeleteSelection(taskId) { return deleteSelections.get(Number(taskId)) || false; }
  async function download(archive, token) {
    if (!fetchImpl || !documentRef || !urlApi) throw new Error('浏览器下载能力不可用。');
    const response = await fetchImpl(archive.download_url, { headers: { 'X-App-Token': token } });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.error || 'ZIP 下载失败。');
    }
    const blob = await response.blob();
    const link = documentRef.createElement('a');
    const objectUrl = urlApi.createObjectURL(blob);
    link.href = objectUrl;
    link.download = archive.filename;
    documentRef.body.append(link);
    link.click();
    link.remove();
    scheduler(() => urlApi.revokeObjectURL(objectUrl), 1000);
  }
  return { progress, archives, deleteSelections, rememberTasks, setProgress,
    clearProgress, setDeleteSelection, getDeleteSelection, download };
}));

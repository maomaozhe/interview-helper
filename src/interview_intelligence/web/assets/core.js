(function (root) {
  "use strict";
  const filterKeys = ["company", "position", "job_family", "language", "topic_l1", "topic_l2",
    "question_type", "response_form", "coding_focus", "annotation_status", "round", "start_date", "end_date", "date_basis"];
  function pickFilters(source) {
    return Object.fromEntries(filterKeys.filter(key => source?.[key] !== undefined
      && source[key] !== null && source[key] !== "").map(key => [key, source[key]]));
  }
  function selectHealthSnapshot(current, incoming) {
    if (!current) return incoming;
    if (incoming.current_revision < current.current_revision ||
        (incoming.current_revision === current.current_revision &&
         incoming.indexed_revision < current.indexed_revision)) return current;
    return incoming;
  }
  function shouldRefreshWorkspace(current, resultMeta) {
    if (!Number.isInteger(resultMeta?.corpus_revision)) return false;
    return !current || resultMeta.corpus_revision > current.current_revision ||
      current.indexed_revision < current.current_revision;
  }
  function matchesDocumentStatus(item, filter) {
    if (!filter) return true;
    const active = item.active_status || (["INCLUDED", "EXCLUDED"].includes(item.status) ? item.status : null);
    if (["INCLUDED", "EXCLUDED"].includes(filter)) return active === filter;
    if (filter === "PENDING") return active === null;
    if (filter === "CHANGED") return item.content_changed === true || item.status === "CHANGED";
    return item.status === filter;
  }
  const api = {pickFilters, selectHealthSnapshot, shouldRefreshWorkspace, matchesDocumentStatus};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.InterviewWorkspace = api;
})(typeof window !== "undefined" ? window : this);

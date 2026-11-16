// Public demo: every browser gets its own simulated day. This keeps a random visitor id
// in the browser and sends it with each request to the server. Locally the server ignores it.
(function () {
  var KEY = "foreman_visitor";
  var id = null;
  try { id = window.localStorage.getItem(KEY); } catch (error) { id = null; }
  if (!id || !/^[A-Za-z0-9_-]{8,64}$/.test(id)) {
    var bytes = new Uint8Array(16);
    window.crypto.getRandomValues(bytes);
    id = Array.prototype.map.call(bytes, function (b) { return ("0" + b.toString(16)).slice(-2); }).join("");
    try { window.localStorage.setItem(KEY, id); } catch (error) { /* private window: id lasts for this page */ }
  }
  var plainFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    var url = typeof input === "string" ? input : (input && input.url) || "";
    if (url.indexOf("/api/") === 0 || url.indexOf(window.location.origin + "/api/") === 0) {
      init = Object.assign({}, init || {});
      var headers = new Headers(init.headers || (typeof input !== "string" && input.headers) || {});
      headers.set("X-Foreman-Visitor", id);
      init.headers = headers;
    }
    return plainFetch(input, init);
  };
})();

let token = sessionStorage.getItem("omniget-token") || "";
const el = (id) => document.getElementById(id);
const states = {queued:"En cola",running:"Descargando",completed:"Completada",failed:"No completada"};
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers:{"Authorization":`Bearer ${token}`,"Content-Type":"application/json",...options.headers}});
  if (!response.ok) {
    if (response.status === 401) disconnect();
    let error;
    try { error = (await response.json()).error; } catch { error = "El servidor no respondió correctamente."; }
    throw new Error(error || "No se pudo completar la solicitud.");
  }
  return response;
}
function disconnect() {
  token = ""; sessionStorage.removeItem("omniget-token");
  el("workspace").hidden = true; el("queue-section").hidden = true; el("access").hidden = false;
}
function node(tag, text, className) {
  const result = document.createElement(tag); result.textContent = text;
  if (className) result.className = className;
  return result;
}
async function refresh() {
  if (!token) return;
  try {
    const jobs = await (await api("/api/jobs")).json();
    el("access").hidden = true; el("workspace").hidden = false; el("queue-section").hidden = false;
    el("count").textContent = `${jobs.length} ${jobs.length === 1 ? "descarga" : "descargas"}`;
    const nodes = jobs.map(job => {
      const card = node("article", "", "job"); const detail = node("div", "");
      let host; try { host = new URL(job.url).hostname; } catch { host = "Descarga"; }
      detail.append(node("div", host, "job-title"),node("p",job.url));
      if (job.error) detail.append(node("p",job.error));
      card.append(detail,node("span",states[job.state] || job.state,`state ${job.state}`));
      if (job.files.length) {
        const files = node("div", "", "files");
        for (const file of job.files) {
          const button = node("button",`${file.name} · ${(file.size / 1048576).toFixed(1)} MB`,"file");
          button.type = "button";
          button.onclick = async () => {
            button.disabled = true;
            try {
              const response = await api(`/api/files/${job.id}/${encodeURIComponent(file.name)}`);
              const blob = URL.createObjectURL(await response.blob());
              const a = document.createElement("a"); a.href = blob; a.download = file.name.split("/").pop(); a.click();
              setTimeout(() => URL.revokeObjectURL(blob),60000);
            } catch(error) { el("message").textContent = error.message; }
            finally { button.disabled = false; }
          };
          files.append(button);
        }
        card.append(files);
      }
      return card;
    });
    el("jobs").replaceChildren(...(nodes.length ? nodes : [node("div","Todavía no hay descargas. Añade tu primer enlace.","empty")]));
    el("message").textContent = "";
  } catch(error) { el("message").textContent = error.message; }
}
el("login").onsubmit = async event => {
  event.preventDefault(); token = el("token").value.trim(); sessionStorage.setItem("omniget-token",token);
  el("token").value = ""; await refresh();
};
el("logout").onclick = disconnect;
el("download").onsubmit = async event => {
  event.preventDefault(); el("submit").disabled = true;
  try {
    await api("/api/jobs",{method:"POST",body:JSON.stringify({url:el("url").value.trim(),quality:Number(el("quality").value),audio_only:el("audio").checked})});
    el("url").value = ""; await refresh();
  } catch(error) { el("message").textContent = error.message; }
  finally { el("submit").disabled = false; }
};
refresh();
setInterval(refresh,5000);

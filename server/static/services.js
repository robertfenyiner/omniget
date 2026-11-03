let udemyCourses = [];
let udemyPage = 1;
let udemyLecturePage = 1;

async function loadServices() {
  const status = await (await api("/api/services/status")).json();
  el("service-status").replaceChildren(...status.services.map(service => {
    const row = node("div", "", "service-row");
    row.append(node("span", `${service.service} · ${service.cookies} cookies guardadas`));
    if (service.cookies) row.append(actionButton("Desconectar", async () => {
      if (!confirm(`¿Eliminar la sesión guardada de ${service.service}?`)) return;
      await api("/api/services/remove", {method:"POST", body:JSON.stringify({service:service.service})});
      await loadServices();
    }));
    return row;
  }));
  el("udemy-status").textContent = status.udemy.verified_at ? "Sesión verificada: tu biblioteca respondió correctamente." :
    (status.udemy.credentials_saved ? "Credenciales guardadas de forma privada. Acceso pendiente de verificar con Udemy." : "Importa una sesión de Udemy para consultar tus cursos.");
  const telegram = await (await api("/api/telegram/status")).json();
  telegramEnabled = telegram.configured;
  el("telegram-status").textContent = telegram.configured ? `Bot conectado en la configuración: @${telegram.username}. Puedes comprobar el envío con el botón de prueba.` : "No hay un bot configurado en este servidor.";
  el("test-telegram").disabled = !telegram.configured;
}

el("cookie-form").onsubmit = async event => {
  event.preventDefault();
  const file = el("cookie-file").files[0];
  if (!file) return;
  if (file.size > 1024 * 1024) { el("message").textContent = "El archivo de cookies supera 1 MB."; return; }
  el("import-cookies").disabled = true;
  try {
    const result = await (await api("/api/services/cookies", {method:"POST", body:JSON.stringify({service:el("cookie-service").value, content:await file.text()})})).json();
    el("cookie-file").value = "";
    el("message").textContent = `Sesión guardada: ${result.count} cookies. La autenticación se comprueba al consultar el servicio.`;
    await loadServices();
  } catch (error) { el("message").textContent = error.message; }
  finally { el("import-cookies").disabled = false; }
};

async function loadUdemyCourses(page) {
  el("verify-udemy").disabled = true;
  el("udemy-result").textContent = "Comprobando la sesión con Udemy…";
  try {
    const data = await (await api("/api/udemy/courses", {method:"POST", body:JSON.stringify({page})})).json();
    udemyCourses = data.courses;
    udemyPage = data.page;
    el("udemy-course").replaceChildren(...udemyCourses.map(course => {
      const option = node("option", course.title); option.value = String(course.id); return option;
    }));
    el("udemy-library").hidden = !udemyCourses.length;
    el("udemy-previous").disabled = udemyPage <= 1;
    el("udemy-next").disabled = !data.has_more;
    el("udemy-result").textContent = `Acceso confirmado · ${data.count} cursos en tu biblioteca · página ${data.page}.`;
    el("udemy-lectures").replaceChildren();
    await loadServices();
  } catch (error) { el("udemy-result").textContent = error.message; }
  finally { el("verify-udemy").disabled = false; }
}
el("verify-udemy").onclick = () => loadUdemyCourses(1);
el("udemy-previous").onclick = () => loadUdemyCourses(Math.max(1, udemyPage - 1));
el("udemy-next").onclick = () => loadUdemyCourses(udemyPage + 1);
async function loadUdemyLectures(page = 1) {
  const course = udemyCourses.find(item => item.id === Number(el("udemy-course").value));
  if (!course) return;
  el("udemy-lectures-button").disabled = true;
  try {
    const data = await (await api("/api/udemy/curriculum", {method:"POST", body:JSON.stringify({course_id:course.id, page})})).json();
    const rows = data.lectures.map(lecture => {
      const row = node("div", "", "service-row");
      row.append(node("span", lecture.title + (lecture.drm ? " · Protegida por DRM" : "")));
      const button = actionButton("Descargar lección", async () => {
        await api("/api/jobs", {method:"POST", body:JSON.stringify({kind:"udemy", quality:720,
          url:`https://www.udemy.com/course/${encodeURIComponent(course.slug)}/learn/lecture/${lecture.id}`})});
        el("udemy-result").textContent = "Lección añadida a la cola.";
        await refresh();
      });
      button.disabled = lecture.drm || String(lecture.type).toLowerCase() !== "video" || !Number.isInteger(lecture.id);
      row.append(button);
      return row;
    });
    if (page === 1) el("udemy-lectures").replaceChildren(...rows);
    else el("udemy-lectures").append(...rows);
    udemyLecturePage = data.page;
    el("udemy-more-lectures").hidden = !data.has_more;
    el("udemy-result").textContent = "Lecciones consultadas. Los vídeos protegidos por DRM no se descargan.";
  } catch (error) { el("udemy-result").textContent = error.message; }
  finally { el("udemy-lectures-button").disabled = false; }
}
el("udemy-lectures-button").onclick = () => loadUdemyLectures(1);
el("udemy-more-lectures").onclick = () => loadUdemyLectures(udemyLecturePage + 1);
el("test-telegram").onclick = async () => {
  el("test-telegram").disabled = true;
  try {
    await api("/api/telegram/test", {method:"POST", body:"{}"});
    el("telegram-status").textContent = "Prueba confirmada: Telegram aceptó el mensaje para tu chat privado.";
  } catch (error) { el("telegram-status").textContent = error.message; }
  finally { el("test-telegram").disabled = false; }
};

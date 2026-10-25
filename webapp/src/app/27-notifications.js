  // ── «Рассылки» (v2.212.0): включить или отключить автосводки. Те же сводки и то же
  // хранение, что у бота (Справка → Рассылки); личные уведомления отключить нельзя.
  function renderNotifications(c, d) {
    c.appendChild(header(d.title, ""));
    c.appendChild(el("p", "footnote footnote-top", d.intro));
    var s = section();
    d.items.forEach(function (it) {
      var li = el("li", "row row-switch");
      var body = el("div", "body");
      body.appendChild(el("div", "name", it.label));
      li.appendChild(body);
      var sw = el("button", "switch" + (it.on ? " is-on" : ""));
      sw.type = "button";
      sw.setAttribute("role", "switch");
      sw.setAttribute("aria-checked", it.on ? "true" : "false");
      sw.setAttribute("aria-label", it.label);
      sw.appendChild(el("span", "switch-knob"));
      sw.addEventListener("click", function () {
        var turnOn = !sw.classList.contains("is-on");
        sw.disabled = true;
        haptic("select");
        post("notifications", { key: it.key, muted: !turnOn }).then(function () {
          sw.classList.toggle("is-on", turnOn);
          sw.setAttribute("aria-checked", turnOn ? "true" : "false");
          sw.disabled = false;
        }, function (err) {
          sw.disabled = false;
          var warn = li.querySelector(".switch-error");
          if (!warn) { warn = el("div", "sub switch-error"); body.appendChild(warn); }
          warn.textContent = err && err.message ? err.message : "Не удалось сохранить.";
        });
      });
      li.appendChild(sw);
      s.list.appendChild(li);
    });
    c.appendChild(s);
    c.appendChild(el("p", "footnote", d.note));
  }

  // ── Графики ────────────────────────────────────────────────────────────────
  function ratingChart(chart) {
    var W = 320, H = 110, L = 2, R = 2, T = 8, B = 16;
    var vals = chart.values;
    var lo = Math.min.apply(null, vals.concat([chart.reference])) - 8;
    var hi = Math.max.apply(null, vals.concat([chart.reference])) + 8;
    function x(i) { return L + (W - L - R) * (vals.length === 1 ? 0.5 : i / (vals.length - 1)); }
    function y(v) { return T + (H - T - B) * (1 - (v - lo) / (hi - lo)); }
    var wrap = el("div", "chart-wrap");
    var s = svg("svg", { "class": "chart", viewBox: "0 0 " + W + " " + H, role: "img" });
    var ref = y(chart.reference);
    s.appendChild(svg("line", { "class": "chart-ref", x1: L, x2: W - R, y1: ref, y2: ref }));
    var pts = vals.map(function (v, i) { return x(i).toFixed(1) + "," + y(v).toFixed(1); });
    var bottom = (H - B).toFixed(1);
    s.appendChild(svg("path", { "class": "chart-area",
      d: "M" + x(0).toFixed(1) + "," + bottom + " L" + pts.join(" L") + " L" + x(vals.length - 1).toFixed(1) + "," + bottom + " Z" }));
    s.appendChild(svg("polyline", { "class": "chart-line", points: pts.join(" "), pathLength: "1" }));
    var last = vals.length - 1;
    var dot = svg("circle", { "class": "chart-dot", cx: x(last), cy: y(vals[last]), r: 3.5 });
    var marker = svg("line", { "class": "chart-marker", x1: 0, x2: 0, y1: T, y2: H - B });
    s.appendChild(marker);
    s.appendChild(dot);
    var first = svg("text", { "class": "chart-label", x: L, y: H - 3 });
    first.textContent = chart.labels[0];
    var end = svg("text", { "class": "chart-label", x: W - R, y: H - 3, "text-anchor": "end" });
    end.textContent = chart.labels[last];
    s.appendChild(first);
    s.appendChild(end);
    wrap.appendChild(s);

    var meta = el("div", "chart-meta");
    var rest = "от " + chart.min.toFixed(1) + " до " + chart.max.toFixed(1) + " · нажмите на график";
    meta.textContent = rest;
    wrap.appendChild(meta);

    function pick(ev) {
      var box = s.getBoundingClientRect();
      if (!box.width) return;
      var px = (ev.clientX - box.left) / box.width * W;
      var i = Math.max(0, Math.min(vals.length - 1, Math.round((px - L) / (W - L - R) * (vals.length - 1))));
      marker.setAttribute("x1", x(i)); marker.setAttribute("x2", x(i));
      marker.classList.add("is-on");
      dot.setAttribute("cx", x(i)); dot.setAttribute("cy", y(vals[i]));
      meta.textContent = chart.labels[i] + " · " + vals[i].toFixed(1);
    }
    function release() {
      marker.classList.remove("is-on");
      dot.setAttribute("cx", x(last)); dot.setAttribute("cy", y(vals[last]));
      meta.textContent = rest;
    }
    s.addEventListener("pointerdown", pick);
    s.addEventListener("pointermove", function (ev) { if (ev.buttons || ev.pointerType === "touch") pick(ev); });
    s.addEventListener("pointerup", release);
    s.addEventListener("pointerleave", release);
    s.addEventListener("pointercancel", release);
    return wrap;
  }

  function heatmap(days) {
    var cell = 12, gap = 3, weeks = Math.ceil(days.length / 7);
    var s = svg("svg", { "class": "heat", viewBox: "0 0 " + weeks * (cell + gap) + " " + 7 * (cell + gap), role: "img" });
    days.forEach(function (d, i) {
      var tier = d.count === null ? "out" : d.count === 0 ? 0 : d.count === 1 ? 1 : d.count <= 3 ? 2 : 3;
      var r = svg("rect", { "class": "heat-" + tier, x: Math.floor(i / 7) * (cell + gap), y: (i % 7) * (cell + gap),
        width: cell, height: cell, rx: 3 });
      if (d.count !== null) { var t = svg("title"); t.textContent = d.date + ": " + d.count; r.appendChild(t); }
      s.appendChild(r);
    });
    return s;
  }

  function radarChart(axes) {
    var size = 300, c = size / 2, rad = 100, n = axes.length;
    var s = svg("svg", { "class": "radar", viewBox: "0 0 " + size + " " + size, role: "img" });
    function point(i, k) {
      var a = -Math.PI / 2 + 2 * Math.PI * i / n;
      return [c + Math.cos(a) * rad * k, c + Math.sin(a) * rad * k];
    }
    [0.25, 0.5, 0.75, 1].forEach(function (k) {
      s.appendChild(svg("polygon", { "class": "radar-grid",
        points: axes.map(function (_, i) { return point(i, k).join(","); }).join(" ") }));
    });
    axes.forEach(function (ax, i) {
      var p = point(i, 1);
      s.appendChild(svg("line", { "class": "radar-grid", x1: c, y1: c, x2: p[0], y2: p[1] }));
      var lp = point(i, 1.22);
      var t = svg("text", { "class": "radar-label", x: lp[0], y: lp[1] + 4, "text-anchor": "middle" });
      t.textContent = ax.name;
      s.appendChild(t);
    });
    s.appendChild(svg("polygon", { "class": "radar-shape",
      points: axes.map(function (ax, i) { return point(i, Math.max(0.02, ax.value / 100)).join(","); }).join(" ") }));
    return s;
  }


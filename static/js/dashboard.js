// Dashboard: two independent tabs (lore stats, statuses), each with its own
// filter panel and Chart.js bar chart updated via the fetch API.
(function () {
    const UNFILTERED_COLOR = "#4a72b8";
    const FILTERED_COLOR = "#c1582d";

    // Configuration for each tab/chart.
    const charts = {
        stats: { endpoint: "/api/stats", canvasId: "stats-chart", label: "Usage count", instance: null },
        statuses: { endpoint: "/api/statuses", canvasId: "statuses-chart", label: "Usage count", instance: null },
    };

    function readFilters(panel) {
        const branchType = panel.querySelector(".f-branch-type").value;
        const bossId = panel.querySelector(".f-boss").value;
        const locationId = panel.querySelector(".f-location").value;
        return { branchType, bossId, locationId };
    }

    function isFiltered(f) {
        return f.branchType !== "all" || f.bossId !== "" || f.locationId !== "";
    }

    function buildQuery(f) {
        const params = new URLSearchParams();
        if (f.branchType !== "all") params.set("branch_type", f.branchType);
        if (f.bossId !== "") params.set("boss_id", f.bossId);
        if (f.locationId !== "") params.set("location_id", f.locationId);
        return params.toString();
    }

    function render(key, data, filtered) {
        const cfg = charts[key];
        const color = filtered ? FILTERED_COLOR : UNFILTERED_COLOR;
        if (cfg.instance) {
            cfg.instance.data.labels = data.labels;
            cfg.instance.data.datasets[0].data = data.values;
            cfg.instance.data.datasets[0].backgroundColor = color;
            cfg.instance.update();
            return;
        }
        const ctx = document.getElementById(cfg.canvasId).getContext("2d");
        cfg.instance = new Chart(ctx, {
            type: "bar",
            data: {
                labels: data.labels,
                datasets: [{ label: cfg.label, data: data.values, backgroundColor: color }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
            },
        });
    }

    function refresh(key) {
        const cfg = charts[key];
        const panel = document.querySelector('.filters[data-target="' + key + '"]');
        const filters = readFilters(panel);
        const query = buildQuery(filters);
        const url = cfg.endpoint + (query ? "?" + query : "");
        fetch(url)
            .then(function (r) { return r.json(); })
            .then(function (data) { render(key, data, isFiltered(filters)); });
    }

    // Wire up filter controls for each panel.
    Object.keys(charts).forEach(function (key) {
        const panel = document.querySelector('.filters[data-target="' + key + '"]');
        panel.querySelectorAll("select").forEach(function (sel) {
            sel.addEventListener("change", function () { refresh(key); });
        });
        panel.querySelector(".f-reset").addEventListener("click", function () {
            panel.querySelectorAll("select").forEach(function (sel) { sel.value = sel.options[0].value; });
            refresh(key);
        });
    });

    // Tab switching.
    document.querySelectorAll(".tab").forEach(function (tab) {
        tab.addEventListener("click", function () {
            const target = tab.dataset.tab;
            document.querySelectorAll(".tab").forEach(function (t) {
                t.classList.toggle("active", t === tab);
            });
            document.querySelectorAll(".tab-panel").forEach(function (panel) {
                panel.hidden = panel.id !== "panel-" + target;
            });
        });
    });

    // Initial unfiltered render for both charts.
    refresh("stats");
    refresh("statuses");
})();

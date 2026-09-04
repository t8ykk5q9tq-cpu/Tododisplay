// --- State ---
let todoItems = [];
let shoppingItems = [];

// --- DOM References ---
const todoList = document.getElementById("todo-list");
const shoppingList = document.getElementById("shopping-list");
const habitsRow = document.getElementById("habits-row");
const clockEl = document.getElementById("clock");

// --- Habits ---
async function fetchHabits() {
    const res = await fetch("/api/habits");
    return res.json();
}
async function toggleHabit(id) {
    await fetch(`/api/habits/${id}/toggle`, { method: "PATCH" });
}
async function addHabit(name) {
    await fetch("/api/habits", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
    });
}
async function deleteHabit(id) {
    await fetch(`/api/habits/${id}`, { method: "DELETE" });
}

function renderHabits(habits) {
    habitsRow.innerHTML = "";
    habits.forEach((h) => {
        const card = document.createElement("div");
        card.className = "habit-card" + (h.done ? " done" : "");

        // --- Top row: checkbox + name + streak ---
        const top = document.createElement("div");
        top.className = "habit-top";

        const box = document.createElement("span");
        box.className = "habit-box";
        box.textContent = h.done ? "\u2713" : "";

        const name = document.createElement("span");
        name.className = "habit-name";
        name.textContent = h.name;

        const streak = document.createElement("span");
        streak.className = "habit-streak";
        streak.textContent = h.streak > 0 ? `\uD83D\uDD25 ${h.streak}` : "";

        top.appendChild(box);
        top.appendChild(name);
        top.appendChild(streak);

        // Tapping the top row toggles today's completion.
        top.addEventListener("click", async () => {
            await toggleHabit(h.id);
            await refreshAll();
        });
        top.addEventListener("contextmenu", async (e) => {
            e.preventDefault();
            if (confirm(`Delete habit "${h.name}"?`)) {
                await deleteHabit(h.id);
                await refreshAll();
            }
        });

        // --- GitHub-style day grid (last ~14 days, oldest -> today) ---
        const grid = document.createElement("div");
        grid.className = "habit-grid";
        (h.history || []).forEach((d) => {
            const cell = document.createElement("span");
            cell.className = "grid-cell" + (d.done ? " on" : "");
            cell.title = d.day;
            grid.appendChild(cell);
        });

        card.appendChild(top);
        card.appendChild(grid);
        habitsRow.appendChild(card);
    });
}

// --- API Helpers ---
async function fetchItems(listType) {
    const res = await fetch(`/api/items/${listType}`);
    return res.json();
}

async function addItem(listType, text) {
    const res = await fetch(`/api/items/${listType}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
    });
    return res.json();
}

async function toggleItem(id) {
    const res = await fetch(`/api/items/${id}/toggle`, { method: "PATCH" });
    return res.json();
}

async function deleteItem(id) {
    await fetch(`/api/items/${id}`, { method: "DELETE" });
}

async function clearDone(listType) {
    await fetch(`/api/items/${listType}/clear-done`, { method: "DELETE" });
}

// --- Rendering ---
function renderList(items, listEl) {
    listEl.innerHTML = "";
    items.forEach((item) => {
        const li = document.createElement("li");
        if (item.done) li.classList.add("done");

        const textSpan = document.createElement("span");
        textSpan.className = "item-text";
        textSpan.textContent = item.text;
        textSpan.addEventListener("click", async () => {
            await toggleItem(item.id);
            await refreshAll();
        });

        const deleteBtn = document.createElement("button");
        deleteBtn.className = "delete-btn";
        deleteBtn.textContent = "\u00d7";
        deleteBtn.setAttribute("aria-label", `Delete ${item.text}`);
        deleteBtn.addEventListener("click", async () => {
            await deleteItem(item.id);
            await refreshAll();
        });

        li.appendChild(textSpan);
        li.appendChild(deleteBtn);
        listEl.appendChild(li);
    });
}

async function refreshAll() {
    todoItems = await fetchItems("todo");
    shoppingItems = await fetchItems("shopping");
    renderList(todoItems, todoList);
    renderList(shoppingItems, shoppingList);
    const habits = await fetchHabits();
    renderHabits(habits);
}

// --- Add Habit Form ---
document.getElementById("add-habit-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = e.target.querySelector("input");
    const name = input.value.trim();
    if (!name) return;
    await addHabit(name);
    input.value = "";
    await refreshAll();
});

// --- Form Handling ---
document.querySelectorAll(".add-form").forEach((form) => {
    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const input = form.querySelector("input");
        const text = input.value.trim();
        if (!text) return;
        const listType = form.dataset.list;
        await addItem(listType, text);
        input.value = "";
        await refreshAll();
    });
});

// --- Clear Done Buttons ---
document.querySelectorAll(".clear-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
        const listType = btn.dataset.list;
        await clearDone(listType);
        await refreshAll();
    });
});

// --- Clock ---
function updateClock() {
    const now = new Date();
    clockEl.textContent = now.toLocaleDateString(undefined, {
        weekday: "long",
        year: "numeric",
        month: "long",
        day: "numeric",
    }) + "  \u2022  " + now.toLocaleTimeString(undefined, {
        hour: "2-digit",
        minute: "2-digit",
    });
}

// --- Live Reload ---
// When live reload is enabled on the server, poll the version endpoint.
// If the frontend files change, reload the page automatically.
let knownVersion = null;
async function checkVersion() {
    try {
        const res = await fetch("/api/version", { cache: "no-store" });
        const data = await res.json();
        if (!data.live_reload) return; // live reload off; do nothing
        if (knownVersion === null) {
            knownVersion = data.version;
        } else if (data.version !== knownVersion) {
            location.reload();
        }
    } catch (e) {
        // Server likely restarting (e.g. after a backend edit). Retry shortly.
    }
}

// --- Auto-refresh (every 30 seconds to stay in sync) ---
setInterval(refreshAll, 30000);
setInterval(updateClock, 1000);
setInterval(checkVersion, 2000);

// --- Init ---
refreshAll();
updateClock();
checkVersion();

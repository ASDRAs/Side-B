import { listYouTubePlaylists } from "./youtubeExport.js";

export function createPlaylistDestination(root, onChange) {
  const modes = [...root.querySelectorAll('[name="playlistDestinationMode"]')];
  const title = root.querySelector("#playlistTitle");
  const search = root.querySelector("#playlistSearch");
  const list = root.querySelector("#playlistOptions");
  const status = root.querySelector("#playlistLoadStatus");
  const reload = root.querySelector("#playlistReload");
  const skip = root.querySelector("#playlistSkipExisting");
  let revision = 0;
  let active = false;
  let loading = false;
  let catalog = null;
  let selectedId = null;
  let initialized = false;
  const mode = () => modes.find((input) => input.checked).value;

  function render() {
    const append = mode() === "append";
    root.querySelector("#newPlaylistFields").hidden = append;
    root.querySelector("#existingPlaylistFields").hidden = !append;
    reload.disabled = loading;
    list.replaceChildren();
    const query = search.value.trim().toLocaleLowerCase();
    const choices = (catalog?.playlists || []).filter((p) => p.title.toLocaleLowerCase().includes(query));
    for (const playlist of choices) {
      const label = document.createElement("label");
      label.className = "playlist-option";
      const radio = document.createElement("input");
      radio.type = "radio";
      radio.name = "existingPlaylist";
      radio.value = playlist.id;
      radio.checked = playlist.id === selectedId;
      radio.addEventListener("change", () => { selectedId = playlist.id; onChange(); });
      const name = document.createElement("span");
      name.textContent = playlist.title;
      const count = document.createElement("small");
      count.textContent = `${playlist.count}곡`;
      label.append(radio, name, count);
      list.append(label);
    }
    if (catalog && !loading) status.textContent = choices.length ? "" : "플레이리스트가 없습니다.";
    onChange();
  }

  async function load() {
    const request = ++revision;
    loading = true;
    catalog = null;
    selectedId = null;
    status.textContent = "내 플레이리스트를 불러오는 중…";
    render();
    let timer;
    try {
      const result = await Promise.race([
        listYouTubePlaylists(),
        new Promise((_, reject) => {
          timer = setTimeout(() => reject(new Error("목록 요청 시간이 초과되었습니다. 다시 불러오세요.")), 90_000);
        }),
      ]);
      if (!active || request !== revision) return;
      catalog = result;
      selectedId = result.recentId;
    } catch (error) {
      if (!active || request !== revision) return;
      status.textContent = error.message || "플레이리스트를 불러오지 못했습니다.";
    } finally {
      clearTimeout(timer);
      if (active && request === revision) { loading = false; render(); }
    }
  }

  for (const input of modes) input.addEventListener("change", () => {
    revision += 1;
    loading = false;
    render();
    if (mode() === "append") void load();
  });
  title.addEventListener("input", onChange);
  search.addEventListener("input", render);
  reload.addEventListener("click", () => { void load(); });

  return {
    reset(defaultTitle) {
      revision += 1;
      active = false;
      initialized = false;
      loading = false;
      catalog = null;
      selectedId = null;
      modes[0].checked = true;
      title.value = defaultTitle.slice(0, 150);
      search.value = "";
      skip.checked = true;
      status.textContent = "";
      root.hidden = true;
    },
    async show() {
      active = true;
      root.hidden = false;
      render();
      modes.find((input) => input.checked).focus();
      if (initialized) {
        if (mode() === "append" && !catalog) void load();
        return;
      }
      initialized = true;
      const request = ++revision;
      try {
        const stored = await chrome.storage.local.get("youtubeLastDestination");
        if (!active || request !== revision) return;
        if (stored.youtubeLastDestination?.mode === "append") {
          modes[1].checked = true;
          if (document.activeElement === modes[0]) modes[1].focus();
        }
      } catch { /* Keep the default destination when local preferences are unavailable. */ }
      if (!active || request !== revision) return;
      render();
      if (mode() === "append") void load();
      else if (document.activeElement === modes[0]) title.focus();
    },
    hide() { active = false; revision += 1; loading = false; root.hidden = true; },
    selection() {
      if (mode() === "create") {
        return title.value.trim() ? { mode: "create", title: title.value.trim() } : null;
      }
      const playlist = !loading && catalog?.playlists.find((p) => p.id === selectedId);
      return playlist ? {
        mode: "append", playlistId: playlist.id, title: playlist.title,
        channelId: catalog.channelId, skipExisting: skip.checked,
      } : null;
    },
  };
}

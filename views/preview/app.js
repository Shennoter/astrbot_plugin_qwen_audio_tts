// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Shennoter
const $ = (id) => document.getElementById(id);
const bridge = window.AstrBotPluginView;
let settings;
let objectUrl;
let busy = false;

function updateCount() {
  const length = Array.from($("text").value.trim()).length;
  $("count").textContent = `${length} / ${settings?.max_chars ?? "—"} 字`;
  $("generate").disabled = busy || !settings?.enabled || !settings?.configured || !length || length > settings.max_chars;
}

function status(message, error = false) {
  $("status").textContent = message;
  $("status").classList.toggle("error", error);
}

async function refresh() {
  busy = true;
  $("refresh").disabled = true;
  updateCount();
  try {
    settings = await bridge.apiGet("preview/settings");
    $("text").value = settings.text;
    $("language").value = settings.language;
    const audio = settings.audio;
    $("settings").textContent = `${settings.model}\n音色：${settings.voice || "未填写"}\n${audio.format} · ${audio.sample_rate} Hz · 音量 ${audio.volume} · 语速 ${audio.rate} · 音调 ${audio.pitch}`;
    status(!settings.enabled ? "试听功能已关闭，请先在插件设置中开启。" : !settings.configured ? "请先在插件的语音服务配置中填写 API Key、业务空间和音色并保存。" : "配置已加载，可以生成试听音频。");
  } catch (error) {
    settings = undefined;
    status(error.message || "配置加载失败，请重载插件后刷新页面。", true);
  } finally {
    busy = false;
    $("refresh").disabled = false;
    updateCount();
  }
}

$("text").addEventListener("input", updateCount);
$("refresh").addEventListener("click", refresh);
$("audio").addEventListener("error", () => status("浏览器无法播放该格式，请下载音频，或在插件中改为 WAV / MP3 后重新生成。", true));
$("preview-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if ($("generate").disabled) return;
  busy = true;
  $("refresh").disabled = true;
  updateCount();
  $("generate").textContent = "正在生成…";
  status("正在生成，请等待；不要重复提交。");
  try {
    const response = await bridge.apiPost("preview/generate", {text: $("text").value, language: $("language").value});
    if (!["audio/wav", "audio/mpeg", "audio/ogg"].includes(response.mime_type)) throw new Error("服务返回了不支持的音频格式。");
    const bytes = Uint8Array.from(atob(response.audio_base64), (c) => c.charCodeAt(0));
    $("audio").pause();
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = URL.createObjectURL(new Blob([bytes], {type: response.mime_type}));
    $("audio").src = objectUrl;
    $("download").href = objectUrl;
    $("download").download = response.filename;
    $("result").hidden = false;
    status("生成完成。点击播放器即可试听，也可以下载音频。");
  } catch (error) {
    status(error.message || "生成失败，请检查插件设置和网络。", true);
  } finally {
    busy = false;
    $("refresh").disabled = false;
    $("generate").textContent = "生成试听音频";
    updateCount();
  }
});

window.addEventListener("beforeunload", () => {
  if (objectUrl) URL.revokeObjectURL(objectUrl);
});

try {
  if (!bridge) throw new Error("请从 AstrBot 插件详情中的“音色试听”可视化页面打开。当前版本需支持插件可视化视图。");
  await bridge.ready();
  await refresh();
} catch (error) {
  status(error.message, true);
}

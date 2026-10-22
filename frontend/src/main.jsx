// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Shennoter
import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  AppBar,
  Avatar,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  CssBaseline,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Divider,
  Drawer,
  FormControlLabel,
  IconButton,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  MenuItem,
  Slider,
  Stack,
  Switch,
  TextField,
  Toolbar,
  Tooltip,
  Typography,
  useMediaQuery,
} from "@mui/material";
import GraphicEqIcon from "@mui/icons-material/GraphicEq";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import MicIcon from "@mui/icons-material/Mic";
import SettingsVoiceIcon from "@mui/icons-material/SettingsVoice";
import TuneIcon from "@mui/icons-material/Tune";
import ShuffleIcon from "@mui/icons-material/Shuffle";
import TranslateIcon from "@mui/icons-material/Translate";
import RecordVoiceOverIcon from "@mui/icons-material/RecordVoiceOver";
import ForumIcon from "@mui/icons-material/Forum";
import BuildIcon from "@mui/icons-material/Build";
import MenuIcon from "@mui/icons-material/Menu";
import SaveIcon from "@mui/icons-material/Save";
import RefreshIcon from "@mui/icons-material/Refresh";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import UploadFileIcon from "@mui/icons-material/UploadFile";
import AddIcon from "@mui/icons-material/Add";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutlined";
import DownloadIcon from "@mui/icons-material/Download";

const bridge = window.AstrBotPluginView;
const clone = (value) => JSON.parse(JSON.stringify(value));
const pages = [
  {
    id: "studio",
    title: "生成与试听",
    icon: GraphicEqIcon,
    description: "调好声音，再让文字开口。",
  },
  {
    id: "clone",
    title: "音色复刻",
    icon: MicIcon,
    description: "用一段清晰录音创建专属音色。",
  },
  {
    id: "connection",
    title: "服务与音色",
    icon: SettingsVoiceIcon,
    groups: ["connection"],
    description: "独立配置 API Key、完整业务空间域名和音色。",
  },
  {
    id: "audio",
    title: "合成参数",
    icon: TuneIcon,
    groups: ["audio"],
    description: "控制音频格式、语速、音调及全局表现。",
  },
  {
    id: "delivery",
    title: "朗读指导",
    icon: RecordVoiceOverIcon,
    groups: ["delivery"],
    description: "自然语言控制语气，精细分段控制停顿和音调。",
  },
  {
    id: "reply",
    title: "随机语音",
    icon: ShuffleIcon,
    groups: ["reply"],
    description: "设置语音回复的概率、长度和冷却。",
  },
  {
    id: "pronunciation",
    title: "发音与词典",
    icon: TranslateIcon,
    groups: ["pronunciation"],
    description: "管理读音修正和持久替换词典。",
  },
  {
    id: "scope",
    title: "会话范围",
    icon: ForumIcon,
    groups: ["scope"],
    description: "在目标会话用 /sid 获取完整 UMO。",
  },
  {
    id: "tools",
    title: "朗读与工具",
    icon: BuildIcon,
    groups: ["command", "natural"],
    description: "管理命令、自然语言调用与权限。",
  },
];
const ranges = {
  volume: [0, 100, 1],
  rate: [0.5, 2, 0.05],
  pitch: [0.5, 2, 0.05],
  probability: [0, 100, 1],
  max_prompt_audio_length: [3, 30, 0.5],
};
const optionLabel = {
  auto: "跟随默认 / 自动",
  zh: "汉语",
  en: "英语",
  ja: "日语",
  all: "全部语言",
};
const drawerWidth = 240;
const cardSx = {
  borderRadius: 3,
  border: 1,
  borderColor: "divider",
  boxShadow: "0 2px 16px rgba(145,158,171,0.08)",
};

function Field({
  group,
  name,
  schema,
  value,
  onChange,
  compact,
  apiKeySet,
  clearKey,
  onClearKey,
}) {
  const meta = schema[group].items[name];
  const path = `${group}.${name}`;
  const numeric = ["int", "float"].includes(meta.type);
  const limit = ranges[name];
  const invalid =
    numeric &&
    (value === "" ||
      !Number.isFinite(Number(value)) ||
      (meta.type === "int" && !Number.isInteger(Number(value))) ||
      (limit && (value < limit[0] || value > limit[1])));
  const hint = compact ? undefined : meta.hint;
  const input = { "data-field": path, "aria-label": meta.description };
  if (meta.type === "bool")
    return (
      <Box>
        <FormControlLabel
          label={meta.description}
          labelPlacement="start"
          sx={{ m: 0, justifyContent: "space-between", width: "100%" }}
          control={
            <Switch
              checked={!!value}
              onChange={(event) => onChange(group, name, event.target.checked)}
              slotProps={{ input }}
            />
          }
        />
        {hint && (
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ display: "block", mt: 0.5 }}
          >
            {hint}
          </Typography>
        )}
      </Box>
    );
  const setValue = (event) => {
    const raw = event.target.value;
    onChange(
      group,
      name,
      meta.type === "list"
        ? raw
            .split("\n")
            .map((item) => item.trim())
            .filter(Boolean)
        : numeric && raw !== ""
          ? Number(raw)
          : raw,
    );
  };
  const field = (
    <TextField
      fullWidth
      size="small"
      label={meta.description}
      type={meta.secret ? "password" : numeric ? "number" : "text"}
      value={Array.isArray(value) ? value.join("\n") : (value ?? "")}
      onChange={setValue}
      select={!!meta.options}
      multiline={["text", "list"].includes(meta.type)}
      minRows={meta.type === "list" ? 3 : 4}
      error={!!invalid}
      helperText={
        invalid
          ? "请检查数值和允许范围。"
          : hint && hint + (meta.type === "list" ? " 每行一项。" : "")
      }
      autoComplete={meta.secret ? "new-password" : "off"}
      placeholder={
        meta.secret
          ? apiKeySet
            ? "已配置，留空保留"
            : "输入 API Key"
          : name === "workspace"
            ? "ws-xxx.cn-beijing.maas.aliyuncs.com"
            : name === "model"
              ? "例如 qwen-audio-3.0-tts-flash"
              : undefined
      }
      slotProps={{
        htmlInput: {
          ...input,
          ...(numeric
            ? {
                step: limit?.[2] ?? (meta.type === "int" ? 1 : "any"),
                min: limit?.[0],
                max: limit?.[1],
              }
            : {}),
        },
      }}
    >
      {meta.options?.map((option) => (
        <MenuItem key={String(option)} value={option}>
          {optionLabel[option] || String(option)}
        </MenuItem>
      ))}
    </TextField>
  );
  return (
    <Box sx={{ minWidth: 0 }}>
      {limit ? (
        <Stack spacing={1.5}>
          <Stack direction="row" spacing={2} sx={{ alignItems: "center" }}>
            <Slider
              aria-label={meta.description}
              min={limit[0]}
              max={limit[1]}
              step={limit[2]}
              value={Number.isFinite(Number(value)) ? Number(value) : limit[0]}
              onChange={(_, next) => onChange(group, name, next)}
              sx={{ flex: 1, mx: 1 }}
            />
            <Box sx={{ width: 100, flexShrink: 0 }}>{field}</Box>
          </Stack>
        </Stack>
      ) : (
        field
      )}
      {meta.secret && (
        <Stack
          direction="row"
          sx={{ alignItems: "center", justifyContent: "space-between", mt: 1 }}
        >
          <Typography variant="caption" color="text.secondary">
            {clearKey
              ? "保存时清除密钥"
              : apiKeySet
                ? "密钥已保存，接口不回显"
                : "尚未配置密钥"}
          </Typography>
          <Button size="small" color="error" onClick={onClearKey}>
            {clearKey ? "取消清除" : "清除密钥"}
          </Button>
        </Stack>
      )}
    </Box>
  );
}

function DirectionEditor({
  guide,
  setGuide,
  segments,
  setSegments,
  text,
  maxSegments,
  maxPause,
  audioDefaults,
}) {
  const update = (index, key, value) =>
    setSegments(
      segments.map((segment, i) =>
        i === index ? { ...segment, [key]: value } : segment,
      ),
    );
  const exact =
    !segments.length ||
    segments.map((segment) => segment.text).join("") === text.trim();
  return (
    <Stack spacing={2}>
      <TextField
        id="delivery-instruction"
        fullWidth
        multiline
        minRows={2}
        label="这句话怎么念（仅本次）"
        value={guide}
        onChange={(event) => setGuide(event.target.value)}
        placeholder="例如：前半句低沉缓慢，后半句激昂，最后几个字语调上扬。"
        helperText="语气、情绪和升降调由模型按指令表现；不会改动全局设置。"
        slotProps={{ htmlInput: { maxLength: 2000 } }}
      />
      <Accordion
        disableGutters
        elevation={0}
        sx={{
          border: 1,
          borderColor: "divider",
          borderRadius: "10px !important",
          "&:before": { display: "none" },
        }}
      >
        <AccordionSummary
          expandIcon={<ExpandMoreIcon />}
          id="segment-editor-toggle"
        >
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            精细停顿与分段音调
          </Typography>
          <Chip
            label={segments.length ? `${segments.length} 段` : "可选"}
            size="small"
            sx={{ ml: 1 }}
          />
        </AccordionSummary>
        <AccordionDetails>
          <Stack spacing={2}>
            <Alert severity="info">
              每段分别调用语音服务并计费，顺序拼成一条
              WAV。段尾添加指定静音；模型自身的停顿仍保留。上限 {maxSegments}{" "}
              段、总静音 {maxPause} 毫秒。
            </Alert>
            {segments.map((segment, index) => (
              <Box
                key={segment.id}
                data-segment={index}
                sx={{
                  p: 2,
                  border: 1,
                  borderColor: "divider",
                  borderRadius: 2,
                }}
              >
                <Stack
                  direction="row"
                  sx={{
                    alignItems: "center",
                    justifyContent: "space-between",
                    mb: 1.5,
                  }}
                >
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>
                    第 {index + 1} 段
                  </Typography>
                  <IconButton
                    size="small"
                    aria-label={`删除第${index + 1}段`}
                    onClick={() =>
                      setSegments(segments.filter((_, i) => i !== index))
                    }
                  >
                    <DeleteOutlineIcon fontSize="small" />
                  </IconButton>
                </Stack>
                <TextField
                  fullWidth
                  size="small"
                  multiline
                  label="本段原文"
                  value={segment.text}
                  onChange={(event) =>
                    update(index, "text", event.target.value)
                  }
                  slotProps={{ htmlInput: { "data-segment-text": index } }}
                />
                <Box
                  sx={{
                    display: "grid",
                    gridTemplateColumns: { xs: "1fr", sm: "repeat(3,1fr)" },
                    gap: 1.5,
                    mt: 2,
                  }}
                >
                  <TextField
                    size="small"
                    type="number"
                    label="段尾停顿（ms）"
                    value={segment.pause_ms}
                    onChange={(event) =>
                      update(index, "pause_ms", Number(event.target.value))
                    }
                    slotProps={{
                      htmlInput: {
                        min: 0,
                        max: 10000,
                        step: 50,
                        "data-segment-pause": index,
                      },
                    }}
                  />
                  <TextField
                    size="small"
                    type="number"
                    label="本段音调"
                    value={segment.pitch}
                    onChange={(event) =>
                      update(index, "pitch", Number(event.target.value))
                    }
                    slotProps={{
                      htmlInput: {
                        min: 0.5,
                        max: 2,
                        step: 0.05,
                        "data-segment-pitch": index,
                      },
                    }}
                  />
                  <TextField
                    size="small"
                    type="number"
                    label="本段语速"
                    value={segment.rate}
                    onChange={(event) =>
                      update(index, "rate", Number(event.target.value))
                    }
                    slotProps={{
                      htmlInput: {
                        min: 0.5,
                        max: 2,
                        step: 0.05,
                        "data-segment-rate": index,
                      },
                    }}
                  />
                </Box>
                <TextField
                  fullWidth
                  size="small"
                  sx={{ mt: 2 }}
                  label="本段语气 / 升降调"
                  value={segment.instruction}
                  onChange={(event) =>
                    update(index, "instruction", event.target.value)
                  }
                  placeholder="例如：低沉，句尾下降 / 激昂，最后上扬"
                />
              </Box>
            ))}
            {!exact && (
              <Alert severity="warning" id="segment-error">
                各段文字连起来必须与原文完全一致，包含标点和空格。
              </Alert>
            )}
            <Stack direction="row" sx={{ gap: 1 }}>
              <Button
                id="add-segment"
                variant="outlined"
                size="small"
                startIcon={<AddIcon />}
                disabled={segments.length >= maxSegments}
                onClick={() =>
                  setSegments([
                    ...segments,
                    {
                      id: crypto.randomUUID(),
                      text: segments.length ? "" : text.trim(),
                      pause_ms: 0,
                      pitch: audioDefaults.pitch,
                      rate: audioDefaults.rate,
                      instruction: "",
                    },
                  ])
                }
              >
                添加一段
              </Button>
              {segments.length > 0 && (
                <Button size="small" onClick={() => setSegments([])}>
                  改回单次合成
                </Button>
              )}
            </Stack>
          </Stack>
        </AccordionDetails>
      </Accordion>
    </Stack>
  );
}

function App() {
  const [data, setData] = useState(null);
  const [draft, setDraft] = useState(null);
  const [revision, setRevision] = useState("");
  const [page, setPage] = useState(
    pages.some((item) => item.id === location.hash.slice(1))
      ? location.hash.slice(1)
      : "studio",
  );
  const [dark, setDark] = useState(
    document.documentElement.dataset.theme === "dark",
  );
  const [mobileOpen, setMobileOpen] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [clearKey, setClearKey] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState({
    message: "正在连接 AstrBot…",
    severity: "info",
  });
  const [discardDialog, setDiscardDialog] = useState(false);
  const [guide, setGuide] = useState("");
  const [segments, setSegments] = useState([]);
  const [result, setResult] = useState(null);
  const [file, setFile] = useState(null);
  const [uploaded, setUploaded] = useState(null);
  const [cloneBusy, setCloneBusy] = useState(false);
  const [cloneMessage, setCloneMessage] = useState(
    "先保存 API Key 和业务空间完整域名，再上传参考录音。",
  );
  const [jobs, setJobs] = useState([]);
  const [jobError, setJobError] = useState("");
  const [querying, setQuerying] = useState(null);
  const audioRef = useRef(null);
  const urlRef = useRef(null);
  const operations = useRef({ busy: false, cloneBusy: false });
  const theme = useMemo(
    () =>
      createTheme({
        palette: {
          mode: dark ? "dark" : "light",
          primary: { main: "#E91E63" },
          background: {
            default: dark ? "#101827" : "#fafbfc",
            paper: dark ? "#1b2639" : "#fff",
          },
        },
        shape: { borderRadius: 10 },
        typography: {
          fontFamily:
            'Inter, "Segoe UI", "Microsoft YaHei", system-ui, sans-serif',
          h4: { fontWeight: 700 },
          h6: { fontWeight: 650 },
          button: { textTransform: "none", fontWeight: 600 },
        },
        components: {
          MuiButton: { defaultProps: { disableElevation: true } },
          MuiFormHelperText: {
            styleOverrides: { root: { marginLeft: 0, lineHeight: 1.7 } },
          },
          MuiCssBaseline: {
            styleOverrides: {
              body: { overflowX: "hidden" },
              code: { overflowWrap: "anywhere" },
            },
          },
        },
      }),
    [dark],
  );
  const desktop = useMediaQuery(theme.breakpoints.up("md"));
  const notify = (message, severity = "info") =>
    setNotice({ message, severity });
  const change = (group, key, value) => {
    setDraft((current) => ({
      ...current,
      [group]: { ...current[group], [key]: value },
    }));
    setDirty(true);
  };
  const navigate = (id) => {
    setPage(id);
    setMobileOpen(false);
    history.replaceState(null, "", `#${id}`);
  };
  const applyData = (next) => {
    setData((old) => ({ ...old, ...next }));
    setDraft(clone(next.config));
    setRevision(next.revision);
    setClearKey(false);
    setDirty(false);
  };

  async function load() {
    if (operations.current.busy) return;
    operations.current.busy = true;
    setBusy(true);
    try {
      const next = await bridge.apiGet("preview/settings");
      applyData(next);
      setUploaded(null);
      notify("设置已加载。试听使用页面草稿，保存后应用到聊天。");
    } catch (error) {
      notify(error.message || "加载失败，请重载插件。", "error");
    } finally {
      operations.current.busy = false;
      setBusy(false);
    }
  }
  useEffect(() => {
    let alive = true,
      off;
    (async () => {
      try {
        if (!bridge)
          throw new Error("请从 AstrBot 的“阿里云TTS助手”插件页面打开。");
        const context = await bridge.ready();
        if (!alive) return;
        if (typeof context.isDark === "boolean") setDark(context.isDark);
        off = bridge.onContext?.((value) => {
          if (typeof value.isDark === "boolean") setDark(value.isDark);
        });
        await load();
      } catch (error) {
        notify(error.message, "error");
      }
    })();
    return () => {
      alive = false;
      off?.();
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    };
  }, []);
  useEffect(() => {
    if (page === "clone" && data?.api_key_set) loadJobs();
  }, [page, data?.revision]);

  function collect() {
    for (const [group, metadata] of Object.entries(data.schema))
      for (const [key, field] of Object.entries(metadata.items)) {
        const value = draft[group][key];
        if (
          ["int", "float"].includes(field.type) &&
          (value === "" ||
            !Number.isFinite(Number(value)) ||
            (field.type === "int" && !Number.isInteger(Number(value))))
        )
          throw new Error(`请检查“${field.description}”的数值。`);
      }
    return clone(draft);
  }
  async function save() {
    if (operations.current.busy) return;
    let config;
    try {
      config = collect();
    } catch (error) {
      notify(error.message, "error");
      return;
    }
    operations.current.busy = true;
    setBusy(true);
    try {
      const next = await bridge.apiPost("settings/save", {
        config,
        revision,
        clear_api_key: clearKey,
      });
      applyData(next);
      notify("全部设置已保存，后续聊天使用新设置。", "success");
    } catch (error) {
      notify(error.message, "error");
    } finally {
      operations.current.busy = false;
      setBusy(false);
    }
  }
  async function generate(event) {
    event?.preventDefault();
    if (operations.current.busy) return;
    let config, plan;
    try {
      config = collect();
      if (segments.length) {
        if (
          segments.map((segment) => segment.text).join("") !==
          config.preview.text.trim()
        )
          throw new Error("分段文字拼接后必须与原文完全一致。");
        plan = { segments: segments.map(({ id, ...segment }) => segment) };
      }
    } catch (error) {
      notify(error.message, "error");
      return;
    }
    operations.current.busy = true;
    setBusy(true);
    notify(
      segments.length
        ? `正在按 ${segments.length} 段合成并拼接，不会自动重试。`
        : "正在合成，请等待…",
    );
    try {
      const response = await bridge.apiPost("preview/generate", {
        text: config.preview.text,
        language: config.preview.language,
        config,
        clear_api_key: clearKey,
        delivery_instruction: guide,
        ...(plan ? { delivery_plan: plan } : {}),
      });
      if (
        !["audio/wav", "audio/mpeg", "audio/ogg"].includes(response.mime_type)
      )
        throw new Error("服务返回了不支持的格式。");
      const bytes = Uint8Array.from(atob(response.audio_base64), (char) =>
        char.charCodeAt(0),
      );
      audioRef.current?.pause();
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
      urlRef.current = URL.createObjectURL(
        new Blob([bytes], { type: response.mime_type }),
      );
      setResult({
        url: urlRef.current,
        filename: response.filename,
        model: config.connection.model,
        format: response.mime_type,
        segments: response.segments || 1,
        pause: response.added_pause_ms || 0,
        retained: config.connection.save_audio,
      });
      notify("生成完成。点击播放器试听；本次指导没有写入全局设置。", "success");
    } catch (error) {
      notify(error.message, "error");
    } finally {
      operations.current.busy = false;
      setBusy(false);
    }
  }
  async function upload() {
    if (!file || operations.current.cloneBusy) return;
    operations.current.cloneBusy = true;
    setCloneBusy(true);
    setCloneMessage("正在上传并校验 WAV…");
    try {
      const next = await bridge.upload("cloning/upload", file);
      setUploaded(next);
      setCloneMessage(
        `校验通过：${next.seconds} 秒 · ${next.sample_rate} Hz · ${next.channels} 声道。可以创建音色。`,
      );
    } catch (error) {
      setUploaded(null);
      setCloneMessage(error.message);
    } finally {
      operations.current.cloneBusy = false;
      setCloneBusy(false);
    }
  }
  async function createVoice() {
    if (!uploaded || operations.current.cloneBusy) return;
    operations.current.cloneBusy = true;
    setCloneBusy(true);
    try {
      const cfg = collect(),
        prefs = cfg.cloning;
      const job = await bridge.apiPost("cloning/create", {
        upload_id: uploaded.upload_id,
        model: cfg.connection.model,
        prefix: prefs.prefix,
        language: prefs.language,
        enable_preprocess: prefs.enable_preprocess,
        enable_volume_normalization: prefs.enable_volume_normalization,
        max_prompt_audio_length: prefs.max_prompt_audio_length,
      });
      setUploaded(null);
      setCloneMessage(
        `任务已记录（${job.status}）。稍后刷新，再查询云端状态；同一上传不会重复创建。`,
      );
      await loadJobs();
    } catch (error) {
      setCloneMessage(error.message);
    } finally {
      operations.current.cloneBusy = false;
      setCloneBusy(false);
    }
  }
  async function loadJobs() {
    try {
      const value = await bridge.apiGet("cloning/jobs");
      setJobs(value.jobs);
      setJobError("");
    } catch (error) {
      setJobError(error.message);
    }
  }
  async function queryJob(job) {
    setQuerying(job.job_id);
    try {
      await bridge.apiPost("cloning/query", { job_id: job.job_id });
      await loadJobs();
    } catch (error) {
      notify(error.message, "error");
    } finally {
      setQuerying(null);
    }
  }
  const field = (group, name, compact = false) =>
    group === "delivery" && name === "provider_id" ? (
      <TextField
        select
        fullWidth
        slotProps={{ htmlInput: { "data-field": "delivery.provider_id" } }}
        label="动态参数规划 LLM"
        value={draft.delivery.provider_id}
        onChange={(e) => change(group, name, e.target.value)}
        helperText="从 AstrBot 已登记的聊天模型中选择；动态模式每次额外调用一次。"
      >
        <MenuItem value="">请选择</MenuItem>
        {(data.providers || []).map((p) => (
          <MenuItem key={p.id} value={p.id}>
            {p.id}
          </MenuItem>
        ))}
      </TextField>
    ) : (
      <Field
        key={`${group}.${name}`}
        group={group}
        name={name}
        schema={data.schema}
        value={draft[group][name]}
        onChange={change}
        compact={compact}
        apiKeySet={data.api_key_set}
        clearKey={clearKey}
        onClearKey={() => {
          setClearKey(!clearKey);
          setDirty(true);
        }}
      />
    );
  const formCard = (group, names) => (
    <Card key={group} sx={cardSx}>
      <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
        <Typography variant="h6" sx={{ fontSize: 17, mb: 3 }}>
          {data.schema[group].description}
        </Typography>
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: { xs: "1fr", sm: "repeat(2,minmax(0,1fr))" },
            gap: 3,
          }}
        >
          {(names || Object.keys(data.schema[group].items)).map((name) => (
            <Box
              key={name}
              sx={{
                minWidth: 0,
                gridColumn:
                  ["text", "list"].includes(
                    data.schema[group].items[name].type,
                  ) || ["api_key", "workspace", "voice"].includes(name)
                    ? "1 / -1"
                    : undefined,
              }}
            >
              {field(group, name)}
            </Box>
          ))}
        </Box>
      </CardContent>
    </Card>
  );
  const current = pages.find((item) => item.id === page) || pages[0];
  const ready =
    draft &&
    (draft.connection.api_key || (data.api_key_set && !clearKey)) &&
    draft.connection.workspace &&
    draft.connection.voice;
  const length = draft ? Array.from(draft.preview.text.trim()).length : 0;
  const sidebar = (
    <Box
      sx={{
        p: 2.5,
        display: "flex",
        flexDirection: "column",
        minHeight: "100%",
      }}
    >
      <Stack
        direction="row"
        spacing={1.5}
        sx={{ alignItems: "center", mb: 4, mt: 1 }}
      >
        <Avatar variant="rounded" sx={{ bgcolor: "#E91E63" }}>
          <GraphicEqIcon />
        </Avatar>
        <Box>
          <Typography sx={{ fontWeight: 700, fontSize: 16 }}>
            阿里云TTS助手
          </Typography>
          <Typography sx={{ fontSize: 9, letterSpacing: 2, color: "#9eafcc" }}>
            VOICE WORKSPACE
          </Typography>
        </Box>
      </Stack>
      <Typography variant="overline" sx={{ color: "#919eab", ml: 1 }}>
        工作台
      </Typography>
      <List dense disablePadding>
        {pages.map((item, index) => (
          <React.Fragment key={item.id}>
            {index === 2 && (
              <Typography
                component="div"
                variant="overline"
                sx={{ color: "#919eab", mt: 3, ml: 1 }}
              >
                插件配置
              </Typography>
            )}
            <ListItemButton
              data-go={item.id}
              selected={page === item.id}
              onClick={() => navigate(item.id)}
              sx={{
                borderRadius: 2,
                mb: 0.5,
                py: 1.15,
                color: "#637381",
                "&.Mui-selected": { bgcolor: "#fce4ec", color: "#E91E63" },
                "&.Mui-selected:hover": { bgcolor: "#f8bbd0" },
              }}
            >
              <ListItemIcon sx={{ color: "inherit", minWidth: 34 }}>
                <item.icon fontSize="small" />
              </ListItemIcon>
              <ListItemText
                primary={item.title}
                slotProps={{ primary: { fontSize: 13 } }}
              />
            </ListItemButton>
          </React.Fragment>
        ))}
      </List>
      <Box sx={{ mt: "auto", pt: 4, ml: 1 }}>
        <Chip
          size="small"
          label="Material UI · v0.6.1"
          variant="outlined"
          sx={{ color: "#a8b9d3", borderColor: "#41516c" }}
        />
      </Box>
    </Box>
  );

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <Box sx={{ minHeight: "100vh", display: "flex" }}>
        <Drawer
          variant={desktop ? "permanent" : "temporary"}
          open={desktop || mobileOpen}
          onClose={() => setMobileOpen(false)}
          slotProps={{
            paper: {
              sx: {
                width: drawerWidth,
                bgcolor: "#fff",
                color: "#212b36",
                borderRight: "1px solid #f0f1f3",
              },
            },
          }}
        >
          {sidebar}
        </Drawer>
        <Box
          sx={{
            minWidth: 0,
            width: desktop ? `calc(100% - ${drawerWidth}px)` : "100%",
            ml: desktop ? `${drawerWidth}px` : 0,
          }}
        >
          <AppBar
            position="sticky"
            color="inherit"
            elevation={0}
            sx={{ borderBottom: 1, borderColor: "divider" }}
          >
            <Toolbar
              sx={{
                gap: 1.5,
                justifyContent: "space-between",
                px: { xs: 1.5, sm: 3.5 },
              }}
            >
              <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
                <IconButton
                  id="menu-toggle"
                  aria-label="展开分类"
                  onClick={() => setMobileOpen(true)}
                  sx={{ display: { md: "none" } }}
                >
                  <MenuIcon />
                </IconButton>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ display: { xs: "none", sm: "block" } }}
                >
                  语音工作空间 /
                </Typography>
                <Typography variant="body2" sx={{ fontWeight: 600 }}>
                  {current.title}
                </Typography>
              </Stack>
              <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
                <Typography
                  id="save-state"
                  variant="caption"
                  color={dirty ? "warning.main" : "text.secondary"}
                  sx={{ display: { xs: "none", sm: "block" } }}
                >
                  {dirty ? "有未保存的调整" : "全部设置已保存"}
                </Typography>
                <Tooltip title="重新加载设置">
                  <span>
                    <IconButton
                      id="refresh"
                      aria-label="重新加载"
                      disabled={busy || cloneBusy}
                      onClick={() => (dirty ? setDiscardDialog(true) : load())}
                    >
                      <RefreshIcon />
                    </IconButton>
                  </span>
                </Tooltip>
                <Button
                  id="save"
                  variant="contained"
                  startIcon={<SaveIcon />}
                  size="small"
                  disabled={!dirty || busy || cloneBusy}
                  onClick={save}
                >
                  保存全部设置
                </Button>
              </Stack>
            </Toolbar>
          </AppBar>
          <Box
            component="main"
            sx={{ maxWidth: 1440, mx: "auto", p: { xs: 1.5, sm: 3.5, xl: 5 } }}
          >
            <Stack
              direction="row"
              spacing={2}
              sx={{
                justifyContent: "space-between",
                alignItems: "center",
                mb: 3,
              }}
            >
              <Box>
                <Typography
                  variant="overline"
                  color="text.secondary"
                  sx={{ fontSize: 10, letterSpacing: 2 }}
                >
                  ALIYUN TEXT TO SPEECH
                </Typography>
                <Typography
                  id="page-title"
                  variant="h4"
                  sx={{ fontSize: { xs: 24, sm: 28 } }}
                >
                  {current.title}
                </Typography>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ mt: 1 }}
                >
                  {current.description}
                </Typography>
              </Box>
              <Chip
                id="service-badge"
                size="small"
                label={ready ? "服务已配置" : "等待配置"}
                variant="outlined"
                color={ready ? "success" : "default"}
              />
            </Stack>
            <Alert
              id="status"
              severity={notice.severity}
              sx={{ mb: 3, overflowWrap: "anywhere" }}
            >
              {notice.message}
            </Alert>
            {!draft ? (
              <Box sx={{ py: 10, textAlign: "center" }}>
                <CircularProgress aria-label="加载设置" />
              </Box>
            ) : (
              <>
                {page === "studio" && (
                  <Box
                    sx={{
                      display: "grid",
                      gridTemplateColumns: {
                        xs: "1fr",
                        lg: "minmax(0,1fr) 310px",
                      },
                      gap: 3,
                    }}
                  >
                    <Card sx={cardSx}>
                      <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
                        <Stack
                          spacing={3}
                          component="form"
                          id="preview-form"
                          onSubmit={generate}
                        >
                          <Stack
                            direction="row"
                            sx={{
                              alignItems: "center",
                              justifyContent: "space-between",
                            }}
                          >
                            <Box>
                              <Typography variant="h6">让文字发声</Typography>
                              <Typography
                                variant="caption"
                                color="text.secondary"
                              >
                                试听使用当前草稿，不会自动保存或重复生成。
                              </Typography>
                            </Box>
                            <Chip
                              label="单句指导"
                              size="small"
                              color="primary"
                              variant="outlined"
                            />
                          </Stack>
                          <TextField
                            fullWidth
                            id="text"
                            label="要朗读的原文"
                            multiline
                            minRows={7}
                            value={draft.preview.text}
                            onChange={(event) =>
                              change("preview", "text", event.target.value)
                            }
                            helperText={
                              <span id="count">
                                {length} / {draft.reply.max_chars} 字 ·
                                只朗读，不自动翻译
                              </span>
                            }
                            error={length > draft.reply.max_chars}
                          />
                          {draft.delivery.mode !== "固定参数" && (
                            <DirectionEditor
                              guide={guide}
                              setGuide={setGuide}
                              segments={segments}
                              setSegments={setSegments}
                              text={draft.preview.text}
                              maxSegments={draft.delivery.max_segments}
                              maxPause={draft.delivery.max_total_pause_ms}
                              audioDefaults={draft.audio}
                            />
                          )}
                          <Stack
                            direction={{ xs: "column", sm: "row" }}
                            spacing={2}
                            sx={{
                              justifyContent: "space-between",
                              alignItems: { sm: "center" },
                            }}
                          >
                            <TextField
                              select
                              size="small"
                              id="language"
                              label="朗读语言"
                              value={draft.preview.language}
                              onChange={(event) =>
                                change(
                                  "preview",
                                  "language",
                                  event.target.value,
                                )
                              }
                              sx={{ minWidth: 180 }}
                            >
                              {["auto", "zh", "en", "ja"].map((code) => (
                                <MenuItem key={code} value={code}>
                                  {optionLabel[code]}
                                </MenuItem>
                              ))}
                            </TextField>
                            <Button
                              id="generate"
                              variant="contained"
                              size="large"
                              type="submit"
                              startIcon={
                                busy ? (
                                  <CircularProgress size={16} color="inherit" />
                                ) : (
                                  <PlayArrowIcon />
                                )
                              }
                              disabled={
                                busy ||
                                !ready ||
                                !draft.preview.enabled ||
                                !length ||
                                length > draft.reply.max_chars
                              }
                            >
                              {busy ? "正在生成…" : "生成音频"}
                            </Button>
                          </Stack>
                          <Typography variant="caption" color="text.secondary">
                            点击后调用语音服务。分段模式每段单独合成并计费，最终返回一条
                            WAV；不依赖模型的 SSML 支持。
                          </Typography>
                          {result && (
                            <Box id="result">
                              <Divider sx={{ mb: 2 }} />
                              <Stack
                                direction="row"
                                sx={{
                                  justifyContent: "space-between",
                                  alignItems: "center",
                                }}
                              >
                                <Typography variant="subtitle2">
                                  生成结果
                                </Typography>
                                <Button
                                  id="download"
                                  component="a"
                                  href={result.url}
                                  download={result.filename}
                                  size="small"
                                  startIcon={<DownloadIcon />}
                                >
                                  下载音频
                                </Button>
                              </Stack>
                              <Box
                                component="audio"
                                id="audio"
                                ref={audioRef}
                                src={result.url}
                                controls
                                preload="metadata"
                                sx={{ width: "100%", mt: 1.5 }}
                                onError={() =>
                                  notify(
                                    "浏览器无法播放此格式，请下载音频或改用 WAV / MP3。",
                                    "error",
                                  )
                                }
                              />
                              <Typography
                                variant="caption"
                                color="text.secondary"
                                sx={{ display: "block", mt: 1 }}
                              >
                                {result.model} · {result.segments} 段 · 追加静音{" "}
                                {result.pause} ms ·{" "}
                                {result.retained
                                  ? "已按保留设置保存本地"
                                  : "服务端临时文件已清理"}
                              </Typography>
                            </Box>
                          )}
                        </Stack>
                      </CardContent>
                    </Card>
                    <Stack spacing={3}>
                      <Card sx={cardSx}>
                        <CardContent sx={{ textAlign: "center", p: 3 }}>
                          <Avatar
                            sx={{
                              width: 64,
                              height: 64,
                              mx: "auto",
                              bgcolor: "primary.main",
                              mb: 2,
                            }}
                          >
                            <GraphicEqIcon fontSize="large" />
                          </Avatar>
                          <Typography variant="h6" sx={{ fontSize: 16 }}>
                            当前音色
                          </Typography>
                          <Typography
                            id="current-model"
                            variant="caption"
                            color="text.secondary"
                            sx={{ display: "block", my: 1 }}
                          >
                            {draft.connection.model}
                          </Typography>
                          <Box
                            component="code"
                            id="current-voice"
                            sx={{
                              display: "block",
                              p: 1.5,
                              bgcolor: "action.hover",
                              borderRadius: 1.5,
                              fontSize: 11,
                            }}
                          >
                            {draft.connection.voice || "请填写音色 ID"}
                          </Box>
                          <Button
                            size="small"
                            onClick={() => navigate("connection")}
                            sx={{ mt: 1 }}
                          >
                            编辑服务与音色 →
                          </Button>
                        </CardContent>
                      </Card>
                      <Card sx={cardSx}>
                        <CardContent sx={{ p: 3 }}>
                          <Typography
                            variant="subtitle1"
                            sx={{ fontWeight: 650, mb: 3 }}
                          >
                            声音微调
                          </Typography>
                          <Stack spacing={3}>
                            {[
                              "volume",
                              "rate",
                              "pitch",
                              "format",
                              "sample_rate",
                            ].map((key) => field("audio", key, true))}
                          </Stack>
                        </CardContent>
                      </Card>
                    </Stack>
                  </Box>
                )}
                {current.groups && (
                  <Stack spacing={3}>
                    {current.groups.map((group) => formCard(group))}
                    {page === "delivery" && (
                      <Alert severity="info">
                        在聊天中可说：“用低沉语气读‘你好，’，停半秒，再提高音调、激昂地读‘出发吧！’”。聊天模型需要实际调用
                        qwen_speak。情绪与升降调是模型表现，分段停顿是插件追加的静音。
                      </Alert>
                    )}
                  </Stack>
                )}
                {page === "clone" && (
                  <Stack spacing={3}>
                    <Box
                      sx={{
                        display: "grid",
                        gridTemplateColumns: {
                          xs: "1fr",
                          lg: "1fr",
                        },
                        gap: 3,
                      }}
                    >
                      <Card sx={cardSx}>
                        <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
                          <Stack spacing={2.5}>
                            <Typography variant="h6">
                              从参考录音创建音色
                            </Typography>
                            <Alert severity="info">
                              上传只校验文件；点击创建后才调用云端。公网部署先配置网关上传大小限制。
                            </Alert>
                            <Button
                              component="label"
                              variant="outlined"
                              startIcon={<UploadFileIcon />}
                              sx={{ py: 4, borderStyle: "dashed" }}
                            >
                              选择 WAV 参考录音
                              <input
                                id="wav-file"
                                type="file"
                                hidden
                                accept=".wav,audio/wav"
                                onChange={(event) => {
                                  const next = event.target.files[0];
                                  setUploaded(null);
                                  if (
                                    next &&
                                    (!next.name
                                      .toLowerCase()
                                      .endsWith(".wav") ||
                                      next.size > 10 * 1024 * 1024)
                                  ) {
                                    setFile(null);
                                    setCloneMessage(
                                      "请选择不超过 10 MiB 的 WAV 文件。",
                                    );
                                  } else setFile(next);
                                }}
                              />
                            </Button>
                            <Typography
                              variant="caption"
                              color="text.secondary"
                            >
                              {file
                                ? `${file.name} · ${(file.size / 1024 / 1024).toFixed(2)} MiB`
                                : "16-bit PCM · 5～60 秒 · ≥ 16 kHz · 单/双声道 · ≤ 10 MiB"}
                            </Typography>
                            <Stack
                              direction="row"
                              sx={{ gap: 1, flexWrap: "wrap" }}
                            >
                              <Button
                                id="upload"
                                variant="outlined"
                                disabled={!file || cloneBusy}
                                onClick={upload}
                              >
                                上传并校验
                              </Button>
                              <Button
                                id="create-voice"
                                variant="contained"
                                disabled={!uploaded || cloneBusy}
                                onClick={createVoice}
                              >
                                创建云端音色
                              </Button>
                            </Stack>
                            <Typography
                              id="clone-status"
                              variant="body2"
                              color="text.secondary"
                              role="status"
                            >
                              {cloneMessage}
                            </Typography>
                            <Typography
                              variant="caption"
                              color="text.secondary"
                            >
                              只上传有权使用的声音。录音暂存在非公开目录，创建结束后删除，未提交录音
                              15 分钟后过期。
                            </Typography>
                            <Divider />
                            {field("connection", "model")}
                            <Box
                              sx={{
                                display: "grid",
                                gridTemplateColumns: {
                                  xs: "1fr",
                                  sm: "1fr 1fr",
                                },
                                gap: 2.5,
                              }}
                            >
                              {[
                                "prefix",
                                "language",
                                "max_prompt_audio_length",
                                "enable_preprocess",
                                "enable_volume_normalization",
                              ].map((key) => field("cloning", key))}
                            </Box>
                          </Stack>
                        </CardContent>
                      </Card>
                    </Box>
                    <Card sx={cardSx}>
                      <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
                        <Stack
                          direction="row"
                          sx={{
                            justifyContent: "space-between",
                            alignItems: "center",
                            mb: 2,
                          }}
                        >
                          <Typography variant="h6">音色任务</Typography>
                          <Button
                            id="refresh-jobs"
                            startIcon={<RefreshIcon />}
                            onClick={loadJobs}
                          >
                            刷新任务
                          </Button>
                        </Stack>
                        <Stack id="jobs" spacing={2}>
                          {jobError && (
                            <Alert severity="error">{jobError}</Alert>
                          )}
                          {!jobs.length && !jobError && (
                            <Typography variant="body2" color="text.secondary">
                              暂无任务。
                            </Typography>
                          )}
                          {jobs.map((job) => (
                            <Box
                              key={job.job_id}
                              data-job={job.job_id}
                              sx={{
                                border: 1,
                                borderColor: "divider",
                                p: 2,
                                borderRadius: 2,
                              }}
                            >
                              <Stack
                                direction="row"
                                spacing={1}
                                sx={{ justifyContent: "space-between" }}
                              >
                                <Typography
                                  variant="subtitle2"
                                  sx={{ overflowWrap: "anywhere" }}
                                >
                                  {job.model}
                                </Typography>
                                <Chip
                                  size="small"
                                  label={job.status}
                                  color={
                                    job.status === "OK" ? "success" : "default"
                                  }
                                />
                              </Stack>
                              <Box
                                component="code"
                                sx={{ display: "block", my: 1.5, fontSize: 12 }}
                              >
                                {job.voice_id ||
                                  job.error ||
                                  "正在创建，请稍后刷新。"}
                              </Box>
                              <Typography
                                variant="caption"
                                color="text.secondary"
                              >
                                {new Date(job.created * 1000).toLocaleString()}{" "}
                                · {job.prefix}
                              </Typography>
                              <Stack
                                direction="row"
                                spacing={1}
                                sx={{ mt: 1.5 }}
                              >
                                {job.voice_id && (
                                  <Button
                                    size="small"
                                    variant="outlined"
                                    disabled={querying === job.job_id}
                                    onClick={() => queryJob(job)}
                                  >
                                    查询云端状态
                                  </Button>
                                )}
                                {job.status === "OK" && (
                                  <Button
                                    size="small"
                                    variant="contained"
                                    onClick={() => {
                                      change("connection", "model", job.model);
                                      change(
                                        "connection",
                                        "voice",
                                        job.voice_id,
                                      );
                                      navigate("studio");
                                      notify(
                                        "音色已填入草稿，可先试听，再保存应用到聊天。",
                                      );
                                    }}
                                  >
                                    填入并试听
                                  </Button>
                                )}
                              </Stack>
                            </Box>
                          ))}
                        </Stack>
                      </CardContent>
                    </Card>
                  </Stack>
                )}
              </>
            )}
            <Typography
              align="center"
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", pt: 4 }}
            >
              阿里云TTS助手 · Material UI · 配置通过 AstrBot 登录桥接访问
            </Typography>
          </Box>
        </Box>
      </Box>
      <Dialog open={discardDialog} onClose={() => setDiscardDialog(false)}>
        <DialogTitle>重新加载设置？</DialogTitle>
        <DialogContent>
          <DialogContentText>
            未保存的配置调整将被丢弃。本句朗读指导和分段计划会保留。
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDiscardDialog(false)}>继续编辑</Button>
          <Button
            color="warning"
            onClick={() => {
              setDiscardDialog(false);
              load();
            }}
          >
            丢弃并加载
          </Button>
        </DialogActions>
      </Dialog>
    </ThemeProvider>
  );
}

createRoot(document.getElementById("root")).render(<App />);

# WebUI 开发

界面使用 React 19、Material UI 9 与 Emotion，源文件为 `src/main.jsx`。用户安装插件时直接运行仓库内已构建的 `views/preview/`，不需要 Node.js、联网下载组件或使用 CDN。

在本目录使用 Node.js 20+ 和 pnpm 10 构建：

```sh
pnpm install --frozen-lockfile --ignore-scripts
pnpm build
```

依赖版本由 `package.json` 和 `pnpm-lock.yaml` 固定。安装时不执行第三方生命周期脚本；esbuild 使用其平台可选依赖中的二进制。更新依赖时应重新检查组件 API 和许可。

构建输出位于 `../views/preview/`：

- `app.js`：压缩后的本地 ESM 包，包含 React、MUI、图标及 Emotion。
- `app.js.LEGAL.txt`：构建器收集的许可声明。
- `THIRD_PARTY_LICENSES.txt`：实际打包的第三方组件版本和许可原文。
- `build-info.json`：运行依赖版本、包大小及 SHA-256，不含本机路径。

源码遵循仓库根目录的 AGPL-3.0-only；第三方库保留各自许可证。入口依赖 AstrBot 注入的 `AstrBotPluginView` bridge，不直接操作宿主 DOM、不保存浏览器密钥、不加载外部字体。

默认构建目标为 Chrome/Edge 117、Firefox 121、Safari 17 及以上。当前适配 MUI 9 的 `sx`、`slotProps` 接口。开发者可运行 `pnpm format` 格式化源码；页面功能测试需提供具有相同接口的测试 bridge，避免向真实云端误发收费请求。

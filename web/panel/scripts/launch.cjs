// Local product bundle: keep dependency-owned Electron.app untouched.
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { execFileSync, spawn } = require("node:child_process");
const panel = path.resolve(__dirname, "..");
const root = path.resolve(panel, "../..");
function prepare() {
  if (process.platform !== "darwin") return require("electron");
  const source = path.join(panel, "node_modules/electron/dist/Electron.app");
  const directory = path.join(root, ".runtime/desktop");
  const bundle = path.join(directory, "BoxAgent.app");
  const executable = path.join(bundle, "Contents/MacOS/BoxAgent");
  const icon = path.join(panel, "public/brand/boxagent.icns");
  const stamp = crypto
    .createHash("sha256")
    .update(fs.readFileSync(__filename))
    .update(fs.readFileSync(path.join(source, "Contents/Info.plist")))
    .update(fs.readFileSync(icon))
    .digest("hex");
  fs.mkdirSync(directory, { recursive: true });
  const marker = path.join(directory, "bundle-version");
  if (
    fs.existsSync(executable) &&
    fs.existsSync(marker) &&
    fs.readFileSync(marker, "utf8") === stamp
  )
    return executable;
  const stage = fs.mkdtempSync(path.join(directory, "prepare-"));
  try {
    const candidate = path.join(stage, "BoxAgent.app");
    // APFS clones keep runtime duplication cheap; copy is limited to the local cache.
    execFileSync("/bin/cp", ["-cR", source, candidate]);
    const info = path.join(candidate, "Contents/Info.plist");
    for (const [key, value] of Object.entries({
      CFBundleDisplayName: "BoxAgent",
      CFBundleName: "BoxAgent",
      CFBundleExecutable: "BoxAgent",
      CFBundleIdentifier: "com.boxagent.desktop",
      CFBundleIconFile: "boxagent.icns",
      LSApplicationCategoryType: "public.app-category.productivity",
      NSCameraUsageDescription: "开启视频通话后，将摄像头画面发送至千问以进行视觉对话；关闭即停止，不在本机录像。",
    }))
      execFileSync("/usr/libexec/PlistBuddy", [
        "-c",
        `Set :${key} ${value}`,
        info,
      ]);
    fs.renameSync(
      path.join(candidate, "Contents/MacOS/Electron"),
      path.join(candidate, "Contents/MacOS/BoxAgent"),
    );
    fs.copyFileSync(
      icon,
      path.join(candidate, "Contents/Resources/boxagent.icns"),
    );
    execFileSync(
      "/usr/bin/codesign",
      ["--force", "--deep", "--sign", "-", candidate],
      { stdio: ["ignore", "ignore", "pipe"] },
    );
    fs.rmSync(bundle, { recursive: true, force: true });
    fs.renameSync(candidate, bundle);
    fs.writeFileSync(marker, stamp);
    return executable;
  } finally {
    fs.rmSync(stage, { recursive: true, force: true });
  }
}
try {
  const executable = prepare();
  if (process.argv.includes("--prepare")) {
    console.log(executable);
  } else {
    const child = spawn(executable, [panel, ...process.argv.slice(2)], {
      stdio: "inherit",
    });
    child.on("error", (error) => {
      console.error(error.message);
      process.exitCode = 1;
    });
    for (const signal of ["SIGINT", "SIGTERM"])
      process.on(signal, () => child.kill(signal));
    child.on("exit", (code, signal) => {
      process.exitCode = code ?? (signal ? 1 : 0);
    });
  }
} catch (error) {
  console.error(`BoxAgent 启动准备失败：${error.message}`);
  process.exitCode = 1;
}

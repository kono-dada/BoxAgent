// Reproducible local SVG exports; no image-generation or network dependency.
const { app, BrowserWindow } = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { execFileSync } = require("node:child_process");
const directory = path.resolve(__dirname, "../public/brand");
app.whenReady().then(async () => {
  const window = new BrowserWindow({
    show: false,
    webPreferences: { sandbox: true },
  });
  try {
    await window.loadURL("about:blank");
    for (const [source, outputs] of [
      [
        "boxagent-icon.svg",
        [
          [1024, "boxagent-icon.png"],
          [512, "boxagent-512.png"],
          [256, "boxagent-256.png"],
          [128, "boxagent-128.png"],
        ],
      ],
      [
        "trayTemplate.svg",
        [
          [18, "trayTemplate.png"],
          [36, "trayTemplate@2x.png"],
        ],
      ],
    ]) {
      const svg = fs.readFileSync(path.join(directory, source), "utf8");
      for (const [size, filename] of outputs) {
        const data = await window.webContents.executeJavaScript(`(async()=>{
          const source = new Image();
          source.src = 'data:image/svg+xml;base64,${Buffer.from(svg).toString("base64")}';
          await source.decode();
          const canvas = document.createElement('canvas');
          canvas.width=canvas.height=${size};
          canvas.getContext('2d').drawImage(source,0,0,${size},${size});
          return canvas.toDataURL('image/png').split(',')[1];
        })()`);
        fs.writeFileSync(
          path.join(directory, filename),
          Buffer.from(data, "base64"),
        );
      }
    }
    if (process.platform === "darwin") {
      const temporary = fs.mkdtempSync(
        path.join(os.tmpdir(), "boxagent-icons-"),
      );
      try {
        const iconset = path.join(temporary, "BoxAgent.iconset");
        fs.mkdirSync(iconset);
        const original = path.join(directory, "boxagent-icon.png");
        for (const size of [16, 32, 128, 256, 512]) {
          for (const scale of [1, 2]) {
            const pixels = size * scale;
            execFileSync(
              "/usr/bin/sips",
              [
                "-z",
                String(pixels),
                String(pixels),
                original,
                "--out",
                path.join(
                  iconset,
                  `icon_${size}x${size}${scale === 2 ? "@2x" : ""}.png`,
                ),
              ],
              { stdio: "ignore" },
            );
          }
        }
        execFileSync("/usr/bin/iconutil", [
          "-c",
          "icns",
          iconset,
          "-o",
          path.join(directory, "boxagent.icns"),
        ]);
      } finally {
        fs.rmSync(temporary, { recursive: true, force: true });
      }
    }
    app.quit();
  } catch (error) {
    console.error(error);
    app.exit(1);
  }
});

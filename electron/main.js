import { app, BrowserWindow, dialog, Menu } from 'electron'
import { spawn } from 'node:child_process'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createServer } from 'node:net'

const __dirname = dirname(fileURLToPath(import.meta.url))
const DEV_URL = process.env.VITE_DEV_SERVER_URL || 'http://127.0.0.1:5173'
const isDev = !app.isPackaged
const BACKEND_PORT = 8756
const BACKEND_HOST = '127.0.0.1'

let backendProcess = null

// 开发时如果 vite 还没起来，页面会加载失败；这里等一会再重试，避免白屏
async function loadWithRetry(win, url, tries = 20) {
  for (let i = 0; i < tries; i += 1) {
    try {
      await win.loadURL(url)
      return
    } catch (error) {
      await new Promise((resolve) => setTimeout(resolve, 500))
    }
  }
  win.loadURL(url)
}

function portInUse(port) {
  return new Promise((resolve) => {
    const server = createServer()
    server.once('error', () => resolve(true))
    server.once('listening', () => server.close(() => resolve(false)))
    server.listen(port, BACKEND_HOST)
  })
}

async function waitForBackend(timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`http://${BACKEND_HOST}:${BACKEND_PORT}/api/wechat/status`)
      if (response.ok) return true
    } catch (error) {
      /* 后端还没起来，继续等 */
    }
    await new Promise((resolve) => setTimeout(resolve, 400))
  }
  return false
}

async function startBackend() {
  // 打包后由应用自己拉起后端 exe；开发时用已经在跑的服务，没跑就提示一下
  if (isDev) {
    const ready = await waitForBackend(3000)
    if (!ready) {
      console.warn('[backend] 开发模式下未检测到后端服务，请先运行 uvicorn')
    }
    return
  }

  const exePath = join(process.resourcesPath, 'backend', 'wechat_service.exe')
  const dataDir = join(app.getPath('userData'), 'data')

  backendProcess = spawn(exePath, [], {
    cwd: dirname(exePath),
    env: {
      ...process.env,
      WECHAT_HOST: BACKEND_HOST,
      WECHAT_PORT: String(BACKEND_PORT),
      WECHAT_DATA_DIR: dataDir,
    },
    windowsHide: true,
    stdio: 'ignore',
  })

  backendProcess.on('exit', (code) => {
    if (code !== 0 && code !== null) {
      console.error(`[backend] 服务退出，代码 ${code}`)
    }
    backendProcess = null
  })

  const ready = await waitForBackend()
  if (!ready) {
    dialog.showErrorBox(
      '后端服务启动失败',
      '微信自动化服务未能在 30 秒内启动，请检查是否有杀毒软件拦截，或端口 8756 被占用。',
    )
  }
}

function stopBackend() {
  if (!backendProcess) return
  // 后端还会拉起截图和 OCR 的子线程，直接 kill 掉整个进程树
  if (process.platform === 'win32') {
    spawn('taskkill', ['/pid', String(backendProcess.pid), '/f', '/t'], {
      windowsHide: true,
    })
  } else {
    backendProcess.kill()
  }
  backendProcess = null
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1100,
    minHeight: 700,
    title: '微信获客',
    backgroundColor: '#f4f6fa',
    webPreferences: {
      preload: join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  if (isDev) {
    loadWithRetry(win, DEV_URL)
  } else {
    win.loadFile(join(__dirname, 'dist', 'index.html'))
  }
}

app.whenReady().then(async () => {
  // 桌面端不需要默认的英文菜单栏
  Menu.setApplicationMenu(null)
  await startBackend()
  createWindow()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

app.on('before-quit', () => {
  stopBackend()
})

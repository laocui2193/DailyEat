'use strict'
const { app, BrowserWindow, Menu, Tray, nativeImage } = require('electron')
const { join } = require('path')

const ICON_PATH = join(__dirname, 'resources', 'icon.png')

let mainWindow = null
let tray = null

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 820,
    height: 620,
    minWidth: 680,
    minHeight: 520,
    show: false,
    title: '随心吃吃 — 香港大学',
    icon: ICON_PATH,          // 任务栏/窗口图标
    autoHideMenuBar: true,
    resizable: true,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true
    }
  })

  Menu.setApplicationMenu(null)

  mainWindow.on('ready-to-show', () => mainWindow.show())
  // 窗口关闭 = 应用完全退出（不留后台进程）
  mainWindow.on('closed', () => { mainWindow = null })

  mainWindow.loadFile('index.html')
}

app.whenReady().then(() => {
  app.setAppUserModelId('com.nearbyeats.app')

  // 系统托盘
  try {
    let trayIcon = null
    try {
      trayIcon = nativeImage.createFromPath(ICON_PATH)
      if (trayIcon.isEmpty()) trayIcon = null
    } catch (_) {}
    // 兜底：文件读不到时用内置 base64 图标
    if (!trayIcon) {
      trayIcon = nativeImage.createFromDataURL(
        'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAABHNCSVQICAgIfAhkiAAAAAlwSFlzAAAAbwAAAG8B8aLcQwAAABl0RVh0U29mdHdhcmUAd3d3Lmlua3NjYXBlLm9yZ5vuPBoAAAEoSURBVDiNpZMxTsNAEEX/rNeOAwUlHVdA4gJcAYkLIOEKNgUlHVdA4gJcAYkLIOEKCAkJJXLs9XpHFIFix55YAvGVpvbnz87M7hIROccYI6WUCCCTK/PO54wxWmu11lrvy7J8BzBEHxFKKbXWWmutjTHGNMZYAFLKXGsNEZxzTpxzQghhvffOOSe9z7VSCim11hoRnfcfxX/qGyICAFBKKaU0L8vyRUR+AAJEVO77Hv1+n2OMSimFiND3PQDA+/7zOwARGWMM7XaLrusSRPx+IKLG7b0HAEBEOI6D87NrOOcQQgAAIAEAIgIR4ZwbpJRQSmm12Wz2X375C6VEJJRS3nsvP3oQEZxzHAA450gAIudcjDEGIgLq9/sX4XA4vFutVu86SRJxOp3et9Vqf9ZutS6yLDsuiiI73aF/BkSYi3KYhrcAAAAASUVORK5CYII='
      )
    }
    tray = new Tray(trayIcon)
    tray.setToolTip('随心吃吃')
    tray.setContextMenu(Menu.buildFromTemplate([
      { label: '显示窗口', click: () => { if (mainWindow) mainWindow.show() } },
      { label: '退出', click: () => { tray = null; app.quit() } }
    ]))
    tray.on('double-click', () => { if (mainWindow) mainWindow.show() })
  } catch (_) { /* 图标加载失败则跳过托盘 */ }

  createWindow()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
    else mainWindow.show()
  })
})

app.on('window-all-closed', () => {
  app.quit()  // Windows：关窗即退出，托盘一起销毁，不留进程
})

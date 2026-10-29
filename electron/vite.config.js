import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// base 用相对路径，Electron 打包后走 file:// 也能加载
export default defineConfig({
  plugins: [vue()],
  base: './',
  server: {
    // 固定监听 IPv4，Electron 里用 127.0.0.1 打开才不会连不上
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
  },
})

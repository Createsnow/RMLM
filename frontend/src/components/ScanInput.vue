<template>
  <div class="scan">
    <el-input ref="inp" v-model="code" size="large" clearable :placeholder="placeholder" @keyup.enter="submit">
      <template #prefix><el-icon><Search /></el-icon></template>
      <template #append>
        <el-button @click="submit">确定</el-button>
      </template>
    </el-input>
    <div class="row">
      <el-button v-if="cameraOk" type="primary" plain @click="toggleCamera">
        <el-icon><Camera /></el-icon>{{ scanning ? '关闭摄像头' : '摄像头扫码' }}
      </el-button>
      <span class="muted">PDA 扫码枪可直接扫入上方输入框；手机可用摄像头扫二维码 / 条码</span>
    </div>
    <video v-show="scanning" ref="video" class="video" playsinline muted />
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'

const props = defineProps({ placeholder: { type: String, default: '扫描或输入仓储码 / 单据号' } })
const emit = defineEmits(['scan'])
const code = ref('')
const inp = ref()
const video = ref()
const scanning = ref(false)
const cameraOk = 'BarcodeDetector' in window && !!navigator.mediaDevices
let stream, timer

function submit() {
  const v = code.value.trim()
  if (v) emit('scan', v)
  code.value = ''
}

async function toggleCamera() {
  if (scanning.value) return stop()
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } })
  } catch {
    ElMessage.error('无法打开摄像头，请检查浏览器权限（需 HTTPS）')
    return
  }
  video.value.srcObject = stream
  await video.value.play()
  scanning.value = true
  // eslint-disable-next-line no-undef
  const detector = new BarcodeDetector()
  timer = setInterval(async () => {
    try {
      const codes = await detector.detect(video.value)
      if (codes.length) {
        stop()
        emit('scan', codes[0].rawValue.trim())
      }
    } catch { /* 帧未就绪 */ }
  }, 300)
}

function stop() {
  clearInterval(timer)
  stream?.getTracks().forEach(t => t.stop())
  scanning.value = false
}

onMounted(() => inp.value?.focus())
onUnmounted(stop)
</script>

<style scoped>
.row { display: flex; align-items: center; gap: 10px; margin-top: 8px; flex-wrap: wrap; }
.video { width: 100%; max-width: 420px; margin-top: 10px; border-radius: 10px; background: #000; }
</style>

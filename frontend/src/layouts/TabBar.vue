<template>
  <!-- 顶栏页签（参考 MTP）：与浏览器标签页一样叠加，用户手动关闭前一直保留；首页固定在最左 -->
  <div class="tabbar">
    <button v-show="overflow" class="arrow" type="button" :disabled="!canLeft" aria-label="向左滚动" @click="scrollBy(-240)">
      <el-icon><ArrowLeft /></el-icon>
    </button>
    <div ref="strip" class="strip" role="tablist" @scroll="measure" @wheel="onWheel">
      <div
        v-for="(tab, i) in tabs.list"
        :key="tab.key"
        class="tab"
        :class="{ on: tab.key === current, dragging: dragKey === tab.key }"
        role="tab"
        tabindex="0"
        :aria-selected="tab.key === current"
        :title="fullTitle(tab)"
        :draggable="i > 0"
        @click="go(tab)"
        @keydown.enter.prevent="go(tab)"
        @keydown.space.prevent="go(tab)"
        @mousedown.middle.prevent
        @auxclick.prevent="$event.button === 1 && close(tab.key)"
        @contextmenu.prevent="openMenu($event, tab.key)"
        @dragstart="onDragStart($event, tab.key)"
        @dragover="onDragOver($event, tab.key)"
        @drop.prevent
        @dragend="dragKey = ''"
      >
        <el-icon v-if="i === 0" :size="14"><House /></el-icon>
        <span class="tt">{{ title(tab) }}</span>
        <button v-if="i > 0" class="x" type="button" aria-label="关闭" @click.stop="close(tab.key)">
          <el-icon :size="12"><Close /></el-icon>
        </button>
      </div>
    </div>
    <button v-show="overflow" class="arrow" type="button" :disabled="!canRight" aria-label="向右滚动" @click="scrollBy(240)">
      <el-icon><ArrowRight /></el-icon>
    </button>
    <el-dropdown trigger="click" placement="bottom-end" @command="c => run(c, current)">
      <el-button text class="more" title="页签操作" aria-label="页签操作"><el-icon><MoreFilled /></el-icon></el-button>
      <template #dropdown>
        <el-dropdown-menu>
          <el-dropdown-item v-for="a in actions(current)" :key="a.cmd" :command="a.cmd" :disabled="a.disabled" :divided="a.divided">
            {{ a.label }}
          </el-dropdown-item>
        </el-dropdown-menu>
      </template>
    </el-dropdown>
    <teleport to="body">
      <div v-if="menu.open" ref="menuEl" class="tab-menu" :style="{ left: `${menu.x}px`, top: `${menu.y}px` }" role="menu" @contextmenu.prevent>
        <template v-for="a in actions(menu.key)" :key="a.cmd">
          <div v-if="a.divided" class="sep" />
          <button type="button" role="menuitem" :disabled="a.disabled" @click="pickMenu(a.cmd)">{{ a.label }}</button>
        </template>
      </div>
    </teleport>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { HOME } from '../tabList'
import { tabs, findTab, closeTab, closeOthers, closeRight, closeAll, refreshTab, moveTab, tabKeyOf } from '../tabs'

/** groupOf(key)：页签所在的侧栏分组名，悬停时显示「分组 / 页面」 */
const props = defineProps({ groupOf: { type: Function, default: () => '' } })

const route = useRoute()
const router = useRouter()
const current = computed(() => tabKeyOf(route))

const title = tab => router.resolve(tab.fullPath).meta.title || tab.fullPath
function fullTitle(tab) {
  const g = props.groupOf(tab.key)
  return g ? `${g} / ${title(tab)}` : title(tab)
}

function go(tab) {
  if (tab.key !== current.value) router.push(tab.fullPath)
}
function close(key) {
  const next = closeTab(key, current.value)
  if (next) router.push(next.fullPath)
}
/** 批量关闭后当前页若已关闭，切到 target（关闭全部时为首页）。 */
function afterBulk(target) {
  if (findTab(current.value)) return
  const tab = findTab(target) ?? findTab(HOME)
  if (tab) router.push(tab.fullPath)
}

function actions(key) {
  const list = tabs.list
  const i = list.findIndex(x => x.key === key)
  return [
    { cmd: 'refresh', label: '刷新', disabled: false, divided: false },
    { cmd: 'close', label: '关闭', disabled: key === HOME, divided: true },
    { cmd: 'closeOthers', label: '关闭其他', disabled: list.every(x => x.key === HOME || x.key === key), divided: false },
    { cmd: 'closeRight', label: '关闭右侧', disabled: i < 0 || i >= list.length - 1, divided: false },
    { cmd: 'closeAll', label: '关闭全部', disabled: list.length <= 1, divided: false }
  ]
}

function run(cmd, key) {
  if (cmd === 'refresh') {
    refreshTab(key)
    const tab = findTab(key)
    if (tab && key !== current.value) router.push(tab.fullPath)
  } else if (cmd === 'close') close(key)
  else if (cmd === 'closeOthers') { closeOthers(key); afterBulk(key) }
  else if (cmd === 'closeRight') { closeRight(key); afterBulk(key) }
  else { closeAll(); afterBulk(HOME) }
}

// ---------------- 右键菜单

const menu = reactive({ open: false, x: 0, y: 0, key: '' })
const menuEl = ref(null)

async function openMenu(e, key) {
  Object.assign(menu, { open: true, x: e.clientX, y: e.clientY, key })
  await nextTick()
  const r = menuEl.value?.getBoundingClientRect()
  if (!r) return
  menu.x = Math.max(4, Math.min(e.clientX, window.innerWidth - r.width - 4))
  menu.y = Math.max(4, Math.min(e.clientY, window.innerHeight - r.height - 4))
}
function closeMenu() { menu.open = false }
function pickMenu(cmd) { closeMenu(); run(cmd, menu.key) }
function onDocDown(e) { if (menu.open && !menuEl.value?.contains(e.target)) closeMenu() }
function onKey(e) { if (e.key === 'Escape') closeMenu() }

// ---------------- 拖动排序（首页固定在最左）

const dragKey = ref('')
function onDragStart(e, key) {
  if (key === HOME) return e.preventDefault()
  dragKey.value = key
  if (e.dataTransfer) { e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', key) }
}
function onDragOver(e, key) {
  if (!dragKey.value) return
  e.preventDefault()
  if (key === dragKey.value || key === HOME) return
  const from = tabs.list.findIndex(x => x.key === dragKey.value)
  const to = tabs.list.findIndex(x => x.key === key)
  moveTab(from, to)
}

// ---------------- 放不下时左右滚动；当前页签自动滚到可见位置

const strip = ref(null)
const overflow = ref(false)
const canLeft = ref(false)
const canRight = ref(false)
let ro

function measure() {
  const s = strip.value
  if (!s) return
  overflow.value = s.scrollWidth > s.clientWidth + 1
  canLeft.value = s.scrollLeft > 0
  canRight.value = s.scrollLeft + s.clientWidth < s.scrollWidth - 1
}
function scrollBy(dx) { strip.value?.scrollBy({ left: dx, behavior: 'smooth' }) }
function onWheel(e) {
  const s = strip.value
  if (!s || !overflow.value || Math.abs(e.deltaX) > Math.abs(e.deltaY)) return
  e.preventDefault()
  s.scrollLeft += e.deltaY
}
function revealNow() {
  const s = strip.value
  const el = s?.querySelector('.tab.on')
  if (!s || !el) return
  const left = el.offsetLeft - s.offsetLeft
  if (left < s.scrollLeft) s.scrollLeft = left - 8
  else if (left + el.offsetWidth > s.scrollLeft + s.clientWidth) s.scrollLeft = left + el.offsetWidth - s.clientWidth + 8
  measure()
}
async function reveal() { await nextTick(); revealNow() }

watch(current, reveal)
watch(() => tabs.list.length, () => nextTick(measure))

onMounted(() => {
  ro = new ResizeObserver(revealNow)
  if (strip.value) ro.observe(strip.value)
  reveal()
  document.addEventListener('mousedown', onDocDown, true)
  document.addEventListener('keydown', onKey)
  window.addEventListener('blur', closeMenu)
  window.addEventListener('resize', closeMenu)
})
onUnmounted(() => {
  ro?.disconnect()
  document.removeEventListener('mousedown', onDocDown, true)
  document.removeEventListener('keydown', onKey)
  window.removeEventListener('blur', closeMenu)
  window.removeEventListener('resize', closeMenu)
})
</script>

<style scoped>
.tabbar { flex: 1; min-width: 0; height: 100%; display: flex; align-items: center; gap: 2px; }
.arrow { flex: none; width: 26px; height: 28px; border: 0; border-radius: 6px; background: none; color: var(--ink2);
  cursor: pointer; display: grid; place-items: center; }
.arrow:hover:not(:disabled) { background: #eef2f7; color: var(--ink); }
.arrow:disabled { opacity: .35; cursor: default; }
.strip { flex: 0 1 auto; min-width: 0; height: 100%; display: flex; align-items: center; gap: 6px; overflow-x: auto; scrollbar-width: none; }
.strip::-webkit-scrollbar { display: none; }
.tab { flex: none; display: flex; align-items: center; gap: 6px; height: 30px; max-width: 180px; padding: 0 6px 0 10px;
  border: 1px solid var(--line); border-radius: 6px; background: #fff; color: #475569; font-size: 13px;
  cursor: pointer; user-select: none; transition: border-color .12s, color .12s, background .12s; }
.tab:first-child { padding-right: 10px; }
.tab:hover { border-color: #a9c8ec; color: var(--brand); }
.tab:focus-visible { outline: 2px solid #7fb0e6; outline-offset: 1px; }
/* 当前页签：浅蓝底、蓝字，与左侧菜单高亮同色系 */
.tab.on { background: #e8f1fb; border-color: #a9c8ec; color: var(--brand); font-weight: 600; }
.tab.dragging { opacity: .45; }
.tt { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.x { flex: none; width: 18px; height: 18px; padding: 0; border: 0; border-radius: 50%; background: none; color: #94a3b8; cursor: pointer;
  display: grid; place-items: center; }
.x:hover { background: rgba(15, 23, 42, .08); color: var(--ink); }
.tab.on .x { color: var(--brand); }
.more { flex: none; width: 30px; height: 30px; padding: 0; color: #475569; }
</style>

<style>
/* 右键菜单（挂在 body 上） */
.tab-menu { position: fixed; z-index: 3000; min-width: 140px; padding: 4px; background: #fff; border: 1px solid var(--line);
  border-radius: 8px; box-shadow: 0 8px 24px rgba(15, 23, 42, .14); }
.tab-menu button { display: block; width: 100%; padding: 7px 12px; border: 0; border-radius: 6px; background: none; color: var(--ink);
  font: inherit; font-size: 13px; text-align: left; cursor: pointer; }
.tab-menu button:hover:not(:disabled), .tab-menu button:focus-visible { background: #e8f1fb; color: var(--brand); outline: none; }
.tab-menu button:disabled { color: #b6c0ce; cursor: default; }
.tab-menu .sep { height: 1px; margin: 4px 0; background: var(--line); }
</style>

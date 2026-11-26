import { onActivated, onMounted } from 'vue'

/**
 * 页面数据加载：打开页面时执行 first（默认同 reload）；
 * 电脑版页面在页签里被缓存，切回该页签时自动执行 reload 重新取数，筛选条件、翻页和填了一半的内容都保留。
 * 手机版不缓存页面，只在打开时加载。
 */
export function useRefresh(reload, first = reload) {
  let entered = false
  onMounted(() => first())
  onActivated(() => {
    // 首次打开时 onActivated 紧跟 onMounted 触发，跳过，避免重复加载
    if (!entered) { entered = true; return }
    reload()
  })
}

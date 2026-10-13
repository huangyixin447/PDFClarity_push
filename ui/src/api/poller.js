// 轮询器:单一 setTimeout 链式调度(不是 setInterval),避免 tick 重叠;
// 失败指数退避(0.8s 起,最大 10s),下一次 tick 永远在上一次结束后才排。
export function createPoller(tick, { base = 800, max = 10000 } = {}) {
  let timer = null;
  let fails = 0;
  let running = false;
  let onError = null;                          // 错误透出回调,由 setOnError 注入

  const interval = () => Math.min(base * 2 ** fails, max);

  async function loop() {
    try {
      await tick();
      fails = 0;
    } catch (e) {
      fails = Math.min(fails + 1, 8);          // 最多退避到 max
      onError?.(e);                             // 通知调用方(store),用于界面提示
    }
    if (running) timer = setTimeout(loop, interval());
  }

  return {
    start() {
      if (running) return;
      running = true;
      loop();
    },
    stop() {
      running = false;
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
    },
    reset() { fails = 0; },
    setOnError(fn) { onError = fn; },          // 用闭包注入,避免 this 绑定问题
  };
}

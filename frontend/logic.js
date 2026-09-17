// 前端纯决策逻辑：与 DOM 解耦的规则函数集合。
// 以 globalThis.AppLogic 挂载：浏览器里供 app.js 使用（须先于 app.js 加载），
// tests/frontend 里供 node --test 直接引用——同一份规则，两处共用。

(function () {
  /**
   * 牌库点击守卫：这次点击是否应当被当作"选底牌"处理。
   * 规则：仅当不在选公共牌模式、且底牌还有空位时才允许；
   * 底牌已选满后（手牌进行中）点击一律忽略，防止误触重开整手牌。
   */
  function heroPickAllowed(state) {
    if (state.gridMode === "board") return false;
    return state.heroCards.some((c) => !c);
  }

  /**
   * 焦点区域：当前该引导用户看哪里。返回 "setup" | "console" | "deck"。
   * 规则：底牌未选齐 → 设置区；轮到发公共牌或正在选公共牌 → 牌库；
   * 其余（行动阶段 / 手牌结束）→ 操作台。
   */
  function nextZone(state) {
    if (!state.heroCards || !state.heroCards.every(Boolean)) return "setup";
    if (!state.view) return "setup";
    if (state.gridMode === "board") return "deck";
    if (!state.view.hand_over && !state.view.actor) return "deck";
    return "console";
  }

  globalThis.AppLogic = { heroPickAllowed, nextZone };
})();

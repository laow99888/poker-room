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

  /**
   * 10：view 请求失败时是否允许"撤销最后一步"自愈。
   * 只有 400（该步操作非法）才回滚；服务/网络故障保留全部历史。
   */
  function shouldRollbackOn(status) {
    return status === 400;
  }

  /**
   * 09：建议响应是否仍属于当前牌局。请求发出后只要操作数或
   * 牌局键（底牌+座位）变了，旧响应就必须丢弃，不得覆盖当前建议。
   */
  function sameHand(before, after) {
    if (!before || !after) return false;
    return before.opsLen === after.opsLen && before.handKey === after.handKey;
  }

  /**
   * 09：牌局键——同一手牌内不变；重开/换桌型/换座位后变化。
   * 用于校验异步响应的归属。
   */
  function handKey(state) {
    return (state.heroCards || []).map((c) => c || "_").join("") +
      "|" + (state.heroPos || "") + "|" + (state.ops ? state.ops.length : 0);
  }

  /**
   * 14：为每手新手牌生成唯一标识，随学习记录提交——内容完全相同的
   * 两手独立牌局靠它区分，服务端按它幂等去重。
   */
  function newHandId() {
    if (globalThis.crypto && typeof crypto.randomUUID === "function") {
      return crypto.randomUUID();
    }
    return "h-" + Date.now().toString(36) + "-" +
      Math.random().toString(36).slice(2, 10);
  }

  /**
   * 15：HTML 转义。对手代号等用户自由输入一律先转义再进 innerHTML，
   * 防止 <img onerror=…> 之类的注入。
   */
  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (ch) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
    ));
  }

  globalThis.AppLogic = { heroPickAllowed, nextZone, shouldRollbackOn, sameHand,
                          handKey, newHandId, escapeHtml };
})();

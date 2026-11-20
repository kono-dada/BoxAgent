const SHORT_REPLY = 110;

function readingTime(text, hardware) {
  return (hardware ? 12000 : 20000) + Math.min(20000, Math.ceil(Math.max(0, text.length - 45) / 8) * 1000);
}

function messageForEvent(type, state) {
  switch (type) {
    case 'notification.pending':
      return { text: state.notification_body || state.notification_text,
        source: state.notification_source === 'wechat' ? '微信' : '提醒', sender: state.notification_sender };
    case 'companion.comment':
      return { text: state.companion_comment, source: state.lol_companion_enabled ? 'LoL' : '鸣潮' };
    case 'game.news':
      return { text: state.game_news_text, source: '鸣潮' };
    case 'front.assistant_text':
    case 'voice.assistant_text':
    case 'voice.assistant_text_done':
      return { text: state.assistant_text };
    default:
      return null;
  }
}

function shouldExpand(message, playing) {
  return !playing && !message.source && (message.text.length > SHORT_REPLY || /\n.{1,40}\n/s.test(message.text));
}

module.exports = { readingTime, messageForEvent, shouldExpand };

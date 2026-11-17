const { isIP } = require('node:net');

function publicSourceUrl(value) {
  if (typeof value !== 'string' || value.length > 8192 || /[\x00-\x20]/.test(value))
    throw Error('网页链接无效');
  let url;
  try { url = new URL(value); } catch { throw Error('网页链接无效'); }
  const host = url.hostname.toLowerCase().replace(/\.$/, '');
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password ||
      !host.includes('.') || isIP(host.replace(/^\[|\]$/g, '')) ||
      host === 'localhost' || /\.(localhost|local|internal)$/.test(host))
    throw Error('只能打开公开网页链接');
  url.hash = '';
  return url.href;
}

module.exports = { publicSourceUrl };

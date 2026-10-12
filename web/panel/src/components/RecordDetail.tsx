import { useEffect, useRef } from 'react';
import { t } from '../i18n';
import { Icon } from './ui';
export interface Detail {
  value: unknown;
  title: string;
  meta: string;
  trigger?: HTMLElement;
}
export function RecordDetail({ detail, close }: { detail: Detail | null; close: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null),
    trigger = useRef<HTMLElement>();
  useEffect(() => {
    if (detail) {
      trigger.current = detail.trigger;
      if (!dialog.current?.open) dialog.current?.showModal();
    } else if (dialog.current?.open) dialog.current.close();
  }, [detail]);
  return (
    <dialog
      id="detail"
      ref={dialog}
      aria-labelledby="detail-title"
      onClose={() => {
        close();
        if (trigger.current?.isConnected) trigger.current.focus({ preventScroll: true });
      }}
      onClick={(event) => {
        if (event.target !== event.currentTarget) return;
        const box = event.currentTarget.getBoundingClientRect();
        if (
          event.clientX < box.left ||
          event.clientX > box.right ||
          event.clientY < box.top ||
          event.clientY > box.bottom
        )
          close();
      }}
    >
      <div className="detail-header">
        <div>
          <p className="eyebrow">DETAIL</p>
          <h2 id="detail-title">{detail?.title || t('记录详情')}</h2>
        </div>
        <button id="close" className="icon-button" aria-label={t('关闭详情')} onClick={close}>
          <Icon name="close" />
        </button>
      </div>
      <p id="detail-meta" className="metadata">
        {detail?.meta}
      </p>
      <pre id="full">
        {detail ? (typeof detail.value === 'string' ? detail.value : JSON.stringify(detail.value, null, 2)) : ''}
      </pre>
    </dialog>
  );
}

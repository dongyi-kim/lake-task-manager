// Bound browser-side work before it becomes server work. Queued requests can
// expire or be cancelled without starting a fetch; permits cover body parsing.
export class RequestPool {
  constructor(capacity = 6, waiting = 32) {
    this.capacity = capacity; this.waiting = waiting; this.active = 0; this.queue = [];
  }
  acquire(signal) {
    if (signal?.aborted) return Promise.reject(signal.reason || new DOMException('Aborted', 'AbortError'));
    if (this.active < this.capacity) { this.active++; return Promise.resolve(this.releaseOnce()); }
    if (this.queue.length >= this.waiting) {
      const error = new Error('요청이 많아 시작하지 못했습니다. 잠시 후 다시 시도해 주세요.');
      Object.assign(error, { status: 429, code: 'browser_capacity', executed: false, retryable: true });
      return Promise.reject(error);
    }
    return new Promise((resolve, reject) => {
      const item = { resolve, signal, abort: () => {
        const index = this.queue.indexOf(item);
        if (index < 0) return;
        this.queue.splice(index, 1);
        signal.removeEventListener('abort', item.abort);
        reject(signal.reason || new DOMException('Aborted', 'AbortError'));
      } };
      this.queue.push(item);
      signal?.addEventListener('abort', item.abort, { once: true });
    });
  }
  releaseOnce() {
    let released = false;
    return () => {
      if (released) return;
      released = true;
      const item = this.queue.shift();
      if (item) {
        item.signal?.removeEventListener('abort', item.abort);
        item.resolve(this.releaseOnce());
      } else this.active--;
    };
  }
}

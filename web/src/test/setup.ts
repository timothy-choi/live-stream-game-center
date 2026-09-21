import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

class SilentEventSource {
  addEventListener() {}
  close() {}
}

Object.defineProperty(globalThis, "EventSource", {
  writable: true,
  configurable: true,
  value: SilentEventSource,
});

afterEach(() => {
  cleanup();
});

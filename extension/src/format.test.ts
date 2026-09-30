import { describe, expect, it } from "vitest";
import { formatDuration, formatRelativeTime } from "./format";

describe("relative capture time", () => {
  it("formats the popup clock", () => expect(formatDuration(61.8)).toBe("01:01"));
  it("switches to hours after one hour", () => expect(formatRelativeTime(3601)).toBe("1:00:01"));
});

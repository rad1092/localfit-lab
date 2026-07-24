"use client";

import { apiUrl, fetchAuth } from "@/lib/api";
import { Check, ChevronDown, LoaderCircle, Search } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

export interface IndustryOption {
  industry_code: string;
  industry_name: string;
  display_label?: string | null;
  selection_path?: string | null;
}

interface IndustryComboboxProps {
  selected: IndustryOption | null;
  initialCode?: string | null;
  disabled?: boolean;
  onSelect: (option: IndustryOption | null) => void;
  onInvalidInitialCode?: (code: string) => void;
}

function normalized(value: string) {
  return value.replace(/\s+/g, "").toLocaleLowerCase("ko-KR");
}

export function IndustryCombobox({
  selected,
  initialCode,
  disabled,
  onSelect,
  onInvalidInitialCode,
}: IndustryComboboxProps) {
  const [query, setQuery] = useState(selected?.industry_name || "");
  const [options, setOptions] = useState<IndustryOption[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [activeIndex, setActiveIndex] = useState(-1);
  const [composing, setComposing] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listboxId = useId();

  useEffect(() => {
    if (!initialCode || selected) return;
    const controller = new AbortController();
    fetchAuth(
      apiUrl(`/chatbot/industry-options?q=${encodeURIComponent(initialCode)}&limit=50`),
      { signal: controller.signal },
    )
      .then(async (response) => {
        if (!response.ok) throw new Error("업종을 확인하지 못했습니다.");
        return response.json() as Promise<IndustryOption[]>;
      })
      .then((items) => {
        if (controller.signal.aborted) return;
        const match = items.find(
          (item) => item.industry_code.toUpperCase() === initialCode.toUpperCase(),
        );
        if (match) {
          setQuery(match.industry_name);
          onSelect(match);
        } else {
          onInvalidInitialCode?.(initialCode);
        }
      })
      .catch((reason) => {
        if (!controller.signal.aborted && !(reason instanceof DOMException && reason.name === "AbortError")) {
          onInvalidInitialCode?.(initialCode);
        }
      });
    return () => controller.abort();
  }, [initialCode, onInvalidInitialCode, onSelect, selected]);

  useEffect(() => {
    if (!open || composing) return;
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setLoading(true);
      setError("");
      try {
        const response = await fetchAuth(
          apiUrl(`/chatbot/industry-options?q=${encodeURIComponent(query.trim())}&limit=${query.trim() ? 80 : 100}`),
          { signal: controller.signal },
        );
        if (!response.ok) throw new Error("업종 목록을 불러오지 못했습니다.");
        const payload = await response.json();
        setOptions(Array.isArray(payload) ? payload : []);
        setActiveIndex(-1);
      } catch (reason) {
        if (!(reason instanceof DOMException && reason.name === "AbortError")) {
          setOptions([]);
          setError(reason instanceof Error ? reason.message : "업종 목록을 불러오지 못했습니다.");
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }, 220);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [composing, open, query]);

  useEffect(() => {
    const close = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, []);

  const choose = (option: IndustryOption) => {
    setQuery(option.industry_name);
    onSelect(option);
    setOpen(false);
    setActiveIndex(-1);
    inputRef.current?.focus();
  };

  return (
    <div ref={rootRef} className="relative">
      <label htmlFor={`${listboxId}-input`} className="text-sm font-bold">분석 업종</label>
      <div className="relative mt-2">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <input
          ref={inputRef}
          id={`${listboxId}-input`}
          role="combobox"
          aria-autocomplete="list"
          aria-expanded={open}
          aria-controls={listboxId}
          aria-activedescendant={activeIndex >= 0 ? `${listboxId}-option-${activeIndex}` : undefined}
          disabled={disabled}
          value={selected?.industry_name ?? query}
          placeholder="카페, 한식 또는 업종 코드 검색"
          autoComplete="off"
          onFocus={() => setOpen(true)}
          onChange={(event) => {
            const value = event.target.value;
            setQuery(value);
            setOpen(true);
            if (selected && normalized(value) !== normalized(selected.industry_name)) onSelect(null);
          }}
          onCompositionStart={() => setComposing(true)}
          onCompositionEnd={(event) => {
            setComposing(false);
            setQuery(event.currentTarget.value);
          }}
          onKeyDown={(event) => {
            if (composing || event.nativeEvent.isComposing) return;
            if (event.key === "ArrowDown") {
              event.preventDefault();
              setOpen(true);
              setActiveIndex((index) => Math.min(index + 1, options.length - 1));
            } else if (event.key === "ArrowUp") {
              event.preventDefault();
              setActiveIndex((index) => Math.max(index - 1, 0));
            } else if (event.key === "Enter" && open && activeIndex >= 0 && options[activeIndex]) {
              event.preventDefault();
              choose(options[activeIndex]);
            } else if (event.key === "Escape" || event.key === "Tab") {
              setOpen(false);
            }
          }}
          className="h-11 w-full rounded-xl border bg-background pl-10 pr-10 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/15 disabled:opacity-50"
        />
        <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
      </div>

      {open && (
        <div className="surface-shadow absolute inset-x-0 top-[74px] z-[120] max-h-72 overflow-y-auto rounded-xl border bg-card p-1.5">
          {loading ? (
            <p className="flex items-center gap-2 px-3 py-3 text-sm text-muted-foreground"><LoaderCircle className="h-4 w-4 animate-spin" /> 업종 검색 중</p>
          ) : error ? (
            <p className="px-3 py-3 text-sm text-destructive" role="alert">{error}</p>
          ) : options.length ? (
            <ul id={listboxId} role="listbox">
              {options.map((option, index) => (
                <li
                  id={`${listboxId}-option-${index}`}
                  key={option.industry_code}
                  role="option"
                  aria-selected={selected?.industry_code === option.industry_code}
                  onMouseDown={(event) => event.preventDefault()}
                  onMouseEnter={() => setActiveIndex(index)}
                  onClick={() => choose(option)}
                  className={`flex min-h-11 cursor-pointer items-center gap-3 rounded-lg px-3 py-2 text-sm ${activeIndex === index ? "bg-accent" : ""}`}
                >
                  <span className="min-w-0 flex-1 font-semibold">{option.industry_name}</span>
                  <span className="shrink-0 font-mono text-xs text-muted-foreground">{option.industry_code}</span>
                  {selected?.industry_code === option.industry_code && <Check className="h-4 w-4 text-primary" />}
                </li>
              ))}
            </ul>
          ) : (
            <p className="px-3 py-3 text-sm text-muted-foreground">일치하는 업종이 없습니다.</p>
          )}
        </div>
      )}
    </div>
  );
}

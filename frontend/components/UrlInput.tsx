"use client";

import { useState, KeyboardEvent } from "react";

interface UrlInputProps {
  urls: string[];
  onChange: (urls: string[]) => void;
}

export default function UrlInput({ urls, onChange }: UrlInputProps) {
  const [value, setValue] = useState("");

  function addUrl() {
    const trimmed = value.trim();
    if (!trimmed) return;
    if (urls.includes(trimmed)) {
      setValue("");
      return;
    }
    onChange([...urls, trimmed]);
    setValue("");
  }

  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") {
      e.preventDefault();
      addUrl();
    }
  }

  function removeUrl(url: string) {
    onChange(urls.filter((u) => u !== url));
  }

  return (
    <div className="url-input">
      <input
        type="text"
        value={value}
        placeholder="Add a URL and press Enter..."
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={handleKeyDown}
      />
      {urls.length > 0 && (
        <div className="chips">
          {urls.map((url) => (
            <span className="chip" key={url}>
              <span className="chip-label">{url}</span>
              <button
                type="button"
                className="chip-remove"
                aria-label={`Remove ${url}`}
                onClick={() => removeUrl(url)}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

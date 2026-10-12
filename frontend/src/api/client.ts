/**
 * api/client.ts
 *
 * Thin typed wrapper around the browser fetch API for all backend REST
 * calls (SDS Section 9.1). No external HTTP client dependency, per the
 * tech stack ("HTTP Client: fetch API, Built-in").
 *
 * Every module that needs backend data (M02, M05, M06, M09) goes through
 * this single client so the request/response shape stays centralized.
 */

import type {
  AIConfirmRequest,
  AIConfirmResponse,
  AIPromptRequest,
  AIResponse,
  HealthResponse,
  ModelResponse,
  PropertyEditRequest,
  PropertyEditResponse,
  ScadRenderRequest,
  ScadRenderResponse,
  TreeResponse,
} from "@/types/api";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly detail?: unknown
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * ApiClient — one instance is created in App.tsx and threaded through
 * context providers (see context/ModelContext.tsx).
 */
export class ApiClient {
  constructor(private readonly baseUrl: string = "/api") {}

  async getHealth(): Promise<HealthResponse> {
    return this.request<HealthResponse>("GET", "/health");
  }

  async getModel(): Promise<ModelResponse> {
    return this.request<ModelResponse>("GET", "/model");
  }

  async getTree(): Promise<TreeResponse> {
    return this.request<TreeResponse>("GET", "/tree");
  }

  async editProperty(guid: string, body: PropertyEditRequest): Promise<PropertyEditResponse> {
    return this.request<PropertyEditResponse>("POST", `/properties/${guid}`, body);
  }

  async renderScad(body: ScadRenderRequest): Promise<ScadRenderResponse> {
    return this.request<ScadRenderResponse>("POST", "/scad/render", body);
  }

  async sendAiPrompt(body: AIPromptRequest): Promise<AIResponse> {
    return this.request<AIResponse>("POST", "/ai/prompt", body);
  }

  /** Day-31: apply one AI proposal the user explicitly confirmed. */
  async confirmAiProposal(body: AIConfirmRequest): Promise<AIConfirmResponse> {
    return this.request<AIConfirmResponse>("POST", "/ai/confirm", body);
  }

  // --------------------------------------------------------------------

  private async request<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });

    if (!response.ok) {
      let detail: unknown;
      try {
        detail = await response.json();
      } catch {
        detail = undefined;
      }
      throw new ApiError(`${method} ${path} failed with status ${response.status}`, response.status, detail);
    }

    return (await response.json()) as T;
  }
}

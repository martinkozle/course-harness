const fallbackError = "Course Harness could not complete that action.";

/** An error response's message, and its code when the server names one. */
export async function responseErrorDetail(
	response: Response,
): Promise<{ message: string; code: string | null }> {
	try {
		const payload = (await response.json()) as {
			detail?:
				| string
				| { message?: string; code?: string }
				| { msg?: string }[];
		};
		const detail = payload.detail;
		// Request validation lists one message per field.
		if (Array.isArray(detail)) {
			const messages = detail
				.map((item) => item.msg?.replace(/^Value error, /, ""))
				.filter(Boolean);
			return {
				message: messages.length ? messages.join(" ") : fallbackError,
				code: null,
			};
		}
		if (typeof detail === "object" && detail !== null) {
			return {
				message: detail.message ?? fallbackError,
				code: detail.code ?? null,
			};
		}
		return { message: detail ?? fallbackError, code: null };
	} catch {
		return { message: fallbackError, code: null };
	}
}

export async function responseError(response: Response): Promise<string> {
	return (await responseErrorDetail(response)).message;
}

export async function responseError(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: string };
    return payload.detail ?? "Course Harness could not complete that action.";
  } catch {
    return "Course Harness could not complete that action.";
  }
}

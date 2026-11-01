import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

function Classroom() {
	return (
		<main>
			<h1>Course Harness Classroom</h1>
			<p>Courses will appear here.</p>
		</main>
	);
}

const root = document.getElementById("root");

if (!root) {
	throw new Error("Course Harness Classroom could not find its application root");
}

createRoot(root).render(
	<StrictMode>
		<Classroom />
	</StrictMode>,
);

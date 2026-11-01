# Derive Quiz correctness and score at read time

Each Answer stores the Release tag, Question ID and chosen Option ID, never whether it was correct. Correctness and score are derived when read, against the latest Release by default, so fixing an answer key in a new Release regrades earlier Answers without a migration; Instructor stats can still break down by Release and flag Answers to Questions that changed or no longer exist. This depends on the Course Agent keeping Question and Option IDs stable when it revises a Quiz. A Learner retries until correct, and attempt k on a Question earns 0.5^(k-1), except that the attempt when only one Option remains untried earns 0 (four Options score 1, 0.5, 0.25, 0); there are no manual overrides.

## How scores are worked out

- Answers are grouped per student, keyed by role and student ID so an Instructor's tries never change a Learner's record, and per Question, keyed by Lecture and Question ID.
- The attempt that counts is the first pick of the correct Option under the grading Release; k is one more than the number of distinct wrong Options picked before it. Picking a wrong Option again costs nothing, picks after the correct one are ignored, and a student who never picks the correct Option scores 0.
- Instructors grade against the served Release by default and may choose any earlier Release still in the mirror, so the same Answers can be scored under either answer key.
- Removing or adding Options changes scores: a picked Option the grading Release no longer has is not counted as a wrong try, and the number of Options decides which attempt earns 0. Instructor stats flag such Answers.

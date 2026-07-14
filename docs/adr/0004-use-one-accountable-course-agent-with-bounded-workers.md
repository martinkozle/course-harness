# Use one accountable course agent with bounded workers

Keep one persistent Course Agent responsible for the conversation and all typed Course mutations, while allowing it to delegate bounded research and drafting to temporary Worker Agents that return proposals and Evidence but cannot mutate authoritative state. This retains the steerability of the proven single-agent prototype while permitting parallel multi-lecture work without adopting course-embroider's rigid batch pipeline or allowing concurrent state writes.

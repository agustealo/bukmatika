export const RESEARCH_HIGHLIGHT_SELECTION_EVENT = "bukmatika:research-highlight-selection";
export const MAX_SELECTED_RESEARCH_HIGHLIGHTS = 8;

export type ResearchHighlightSelectionDetail = {
  highlightIds: string[];
};

export function publishResearchHighlightSelection(highlightIds: string[]): void {
  window.dispatchEvent(
    new CustomEvent<ResearchHighlightSelectionDetail>(RESEARCH_HIGHLIGHT_SELECTION_EVENT, {
      detail: { highlightIds },
    }),
  );
}

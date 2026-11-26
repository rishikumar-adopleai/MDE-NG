import { titleCaseHeading } from './headingCase';

export function HeadingText({ children }: { children: string }) {
  return titleCaseHeading(children);
}

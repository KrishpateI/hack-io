import { Aspects, IAspect, TagManager } from 'aws-cdk-lib';
import { IConstruct } from 'constructs';

/** Tags every taggable resource in scope with Project=SafePath, Environment and Simulated=true. */
export class ProjectTagAspect implements IAspect {
  constructor(private readonly environment: string) {}

  visit(node: IConstruct): void {
    if (TagManager.isTaggable(node)) {
      node.tags.setTag('Project', 'SafePath', 100, true);
      node.tags.setTag('Environment', this.environment, 100, true);
      node.tags.setTag('Simulated', 'true', 100, true);
    }
  }
}

export function applyProjectTags(scope: IConstruct, environment: string): void {
  Aspects.of(scope).add(new ProjectTagAspect(environment));
}

import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { StartList } from '../../src/components/StartList';
import type { Horse } from '../../src/domain/types';

describe('API story independent accessible-number oracle', () => {
  it('keeps original number and name together through sorting and scratched filtering', () => {
    const horses: Horse[] = [
      {
        id: 'A',
        gate: 7,
        name: 'Amber Star',
        driver: 'Driver A',
        trainer: 'Trainer A',
        form: [],
        odds: 4,
      },
      {
        id: 'B',
        gate: 2,
        name: 'Blue Comet',
        driver: 'Driver B',
        trainer: 'Trainer B',
        form: [],
        odds: 6,
        scratched: true,
      },
      {
        id: 'C',
        gate: 11,
        name: 'Copper Moon',
        driver: 'Driver C',
        trainer: 'Trainer C',
        form: [],
        odds: 8,
      },
    ];
    render(
      <StartList
        horses={horses}
        selectedHorseId={null}
        unavailable={false}
        searchText=""
        onSearchChange={vi.fn()}
        onSelect={vi.fn()}
        onRecover={vi.fn()}
      />,
    );
    const verify = () => {
      for (const horse of horses) {
        const radio = within(
          screen.getByTestId(`starter-${horse.id}`),
        ).getByRole('radio');
        expect(radio).toHaveAccessibleName(new RegExp(horse.name));
        expect(radio).toHaveAccessibleName(new RegExp(`\\b${horse.gate}\\b`));
        if (horse.scratched) {
          expect(radio).toHaveAccessibleName(/scratched/i);
          expect(radio).toBeDisabled();
        }
      }
    };
    verify();
    fireEvent.change(screen.getByLabelText('Sort start list'), {
      target: { value: 'descending' },
    });
    verify();
    fireEvent.click(screen.getByLabelText('Hide scratched horses'));
    expect(screen.queryByTestId('starter-B')).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Hide scratched horses'));
    verify();
  });
});

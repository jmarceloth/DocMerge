import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import App from './App';

// Mock the pdfUtils to avoid actual PDF processing during UI tests
vi.mock('./lib/pdfUtils', () => ({
    g3: vi.fn(),
    combinePDFs: vi.fn(),
}));

describe('App Component', () => {
    it('renders the main title', () => {
        render(<App />);
        expect(screen.getByText('DocMerge PDF')).toBeInTheDocument();
        expect(screen.getByText(/Ferramentas profissionais para manipulação de PDF/i)).toBeInTheDocument();
    });

    it('renders the Merge section', () => {
        render(<App />);
        expect(screen.getByRole('heading', { name: 'Aplicar Timbrado' })).toBeInTheDocument();
        expect(screen.getByText('PDF de Conteúdo')).toBeInTheDocument();
        expect(screen.getByText('PDF de Timbrado (Salvo Automaticamente)')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: /Mesclar PDF/i })).toBeInTheDocument();
    });

    it('renders the Combine section', () => {
        render(<App />);
        expect(screen.getByRole('heading', { name: 'Combinar Arquivos' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: /Combinar Arquivos/i })).toBeInTheDocument();
    });

    it('buttons are disabled initially', () => {
        render(<App />);
        const mergeButton = screen.getByRole('button', { name: /Mesclar PDF/i });
        const combineButton = screen.getByRole('button', { name: /Combinar Arquivos/i });

        expect(mergeButton).toBeDisabled();
        expect(combineButton).toBeDisabled();
    });
});

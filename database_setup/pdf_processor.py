"""
PDF Processor for Legal Document RAG System
Handles PDF text extraction, cleaning, and preprocessing for optimal vector storage
"""

import re
import logging
from typing import List, Dict, Tuple
from pathlib import Path
import PyPDF2
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class LegalPDFProcessor:
    """
    Specialized PDF processor for legal documents with advanced preprocessing
    """
    
    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 200):
        """
        Initialize the PDF processor
        
        Args:
            chunk_size: Size of text chunks for embedding
            chunk_overlap: Overlap between chunks to maintain context
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len,
            separators=["\n\n", "\n", ". ", "! ", "? ", " ", ""]
        )
        
        # Legal document specific patterns to clean
        self.legal_patterns = {
            'page_numbers': r'^\s*\d+\s*$',
            'headers_footers': r'^(Page \d+|Chapter \d+|Section \d+)$',
            'empty_lines': r'^\s*$',
            'repeated_spaces': r'\s+',
            'special_chars': r'[^\w\s\.\,\;\:\!\?\-\(\)\[\]\"\'\/]',
            'urls': r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+',
            'emails': r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
            'phone_numbers': r'(\+?1[-.\s]?)?\(?[0-9]{3}\)?[-.\s]?[0-9]{3}[-.\s]?[0-9]{4}',
            'dates': r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b',
            'case_numbers': r'Case No\.?\s*[A-Z0-9\-]+',
            'section_references': r'§\s*\d+\.?\d*',
            'footnote_markers': r'\[\d+\]',
            'table_of_contents': r'^\.{3,}\s*\d+$'
        }
        
    def extract_text_from_pdf(self, pdf_path: str) -> str:
        """
        Extract text from PDF file
        
        Args:
            pdf_path: Path to the PDF file
            
        Returns:
            Extracted text content
        """
        try:
            with open(pdf_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                text = ""
                
                logger.info(f"Processing PDF with {len(pdf_reader.pages)} pages")
                
                for page_num, page in enumerate(pdf_reader.pages):
                    try:
                        page_text = page.extract_text()
                        if page_text.strip():
                            text += f"\n--- Page {page_num + 1} ---\n"
                            text += page_text
                    except Exception as e:
                        logger.warning(f"Error extracting text from page {page_num + 1}: {e}")
                        continue
                        
                return text
                
        except Exception as e:
            logger.error(f"Error reading PDF file {pdf_path}: {e}")
            raise
    
    def clean_text(self, text: str) -> str:
        """
        Clean and preprocess extracted text
        
        Args:
            text: Raw extracted text
            
        Returns:
            Cleaned text
        """
        logger.info("Starting text cleaning process")
        
        # Remove page markers and headers
        lines = text.split('\n')
        cleaned_lines = []
        
        for line in lines:
            line = line.strip()
            
            # Skip empty lines
            if not line:
                continue
                
            # Skip page numbers and headers
            if re.match(self.legal_patterns['page_numbers'], line):
                continue
            if re.match(self.legal_patterns['headers_footers'], line):
                continue
            if re.match(self.legal_patterns['table_of_contents'], line):
                continue
                
            # Clean the line
            line = self._clean_line(line)
            if line:  # Only add non-empty lines
                cleaned_lines.append(line)
        
        # Join lines and perform final cleaning
        cleaned_text = '\n'.join(cleaned_lines)
        
        # Remove URLs, emails, phone numbers
        cleaned_text = re.sub(self.legal_patterns['urls'], '[URL]', cleaned_text)
        cleaned_text = re.sub(self.legal_patterns['emails'], '[EMAIL]', cleaned_text)
        cleaned_text = re.sub(self.legal_patterns['phone_numbers'], '[PHONE]', cleaned_text)
        
        # Normalize whitespace
        cleaned_text = re.sub(self.legal_patterns['repeated_spaces'], ' ', cleaned_text)
        
        # Remove excessive newlines
        cleaned_text = re.sub(r'\n{3,}', '\n\n', cleaned_text)
        
        logger.info(f"Text cleaning completed. Original length: {len(text)}, Cleaned length: {len(cleaned_text)}")
        
        return cleaned_text.strip()
    
    def _clean_line(self, line: str) -> str:
        """
        Clean individual line of text
        
        Args:
            line: Text line to clean
            
        Returns:
            Cleaned line
        """
        # Remove footnote markers
        line = re.sub(self.legal_patterns['footnote_markers'], '', line)
        
        # Remove excessive punctuation
        line = re.sub(r'[.]{3,}', '...', line)
        line = re.sub(r'[-]{3,}', '---', line)
        
        # Normalize quotes
        line = line.replace('"', '"').replace('"', '"')
        line = line.replace(''', "'").replace(''', "'")
        
        # Remove special characters but keep legal symbols
        line = re.sub(self.legal_patterns['special_chars'], ' ', line)
        
        # Normalize whitespace
        line = re.sub(self.legal_patterns['repeated_spaces'], ' ', line)
        
        return line.strip()
    
    def extract_legal_entities(self, text: str) -> Dict[str, List[str]]:
        """
        Extract legal entities and key information from text
        
        Args:
            text: Cleaned text content
            
        Returns:
            Dictionary of extracted entities
        """
        entities = {
            'sections': [],
            'chapters': [],
            'case_references': [],
            'legal_terms': [],
            'statutes': []
        }
        
        # Extract sections and chapters
        section_pattern = r'(?:Section|§)\s*(\d+\.?\d*)'
        entities['sections'] = re.findall(section_pattern, text, re.IGNORECASE)
        
        chapter_pattern = r'Chapter\s*(\d+)'
        entities['chapters'] = re.findall(chapter_pattern, text, re.IGNORECASE)
        
        # Extract case references
        case_pattern = r'([A-Z][a-z]+ v\. [A-Z][a-z]+)'
        entities['case_references'] = re.findall(case_pattern, text)
        
        # Extract legal terms (basic pattern)
        legal_terms = [
            'plaintiff', 'defendant', 'court', 'judge', 'jury', 'trial',
            'evidence', 'testimony', 'witness', 'hearing', 'motion',
            'objection', 'ruling', 'verdict', 'appeal', 'jurisdiction'
        ]
        
        for term in legal_terms:
            if term.lower() in text.lower():
                entities['legal_terms'].append(term)
        
        return entities
    
    def create_chunks(self, text: str, metadata: Dict = None) -> List[Document]:
        """
        Split text into chunks for embedding
        
        Args:
            text: Cleaned text content
            metadata: Additional metadata for chunks
            
        Returns:
            List of Document objects
        """
        logger.info("Creating text chunks for embedding")
        
        # Create base metadata
        base_metadata = {
            'source': 'legal_handbook',
            'document_type': 'legal_guidance',
            'preprocessing_version': '1.0'
        }
        
        if metadata:
            base_metadata.update(metadata)
        
        # Split text into chunks
        chunks = self.text_splitter.split_text(text)
        
        # Create Document objects
        documents = []
        for i, chunk in enumerate(chunks):
            if len(chunk.strip()) > 50:  # Only include substantial chunks
                doc_metadata = base_metadata.copy()
                doc_metadata.update({
                    'chunk_id': i,
                    'chunk_length': len(chunk),
                    'chunk_text_preview': chunk[:100] + '...' if len(chunk) > 100 else chunk
                })
                
                documents.append(Document(
                    page_content=chunk,
                    metadata=doc_metadata
                ))
        
        logger.info(f"Created {len(documents)} text chunks")
        return documents
    
    def process_pdf(self, pdf_path: str) -> Tuple[List[Document], Dict]:
        """
        Complete PDF processing pipeline
        
        Args:
            pdf_path: Path to the PDF file
            
        Returns:
            Tuple of (processed_documents, processing_stats)
        """
        logger.info(f"Starting PDF processing for: {pdf_path}")
        
        # Extract text
        raw_text = self.extract_text_from_pdf(pdf_path)
        
        # Clean text
        cleaned_text = self.clean_text(raw_text)
        
        # Extract entities
        entities = self.extract_legal_entities(cleaned_text)
        
        # Create chunks
        documents = self.create_chunks(cleaned_text, {'entities': entities})
        
        # Create processing statistics
        stats = {
            'original_text_length': len(raw_text),
            'cleaned_text_length': len(cleaned_text),
            'number_of_chunks': len(documents),
            'average_chunk_size': sum(len(doc.page_content) for doc in documents) / len(documents) if documents else 0,
            'entities_found': {k: len(v) for k, v in entities.items()},
            'compression_ratio': len(cleaned_text) / len(raw_text) if raw_text else 0
        }
        
        logger.info(f"PDF processing completed. Stats: {stats}")
        
        return documents, stats

def main():
    """
    Example usage of the PDF processor
    """
    processor = LegalPDFProcessor(chunk_size=1000, chunk_overlap=200)
    
    pdf_path = "data/general_legal_toolkit_handbook_for_vulnerable_witnesses.pdf"
    
    if Path(pdf_path).exists():
        documents, stats = processor.process_pdf(pdf_path)
        
        print(f"Processing completed!")
        print(f"Number of chunks created: {stats['number_of_chunks']}")
        print(f"Average chunk size: {stats['average_chunk_size']:.0f} characters")
        print(f"Text compression ratio: {stats['compression_ratio']:.2%}")
        print(f"Entities found: {stats['entities_found']}")
        
        # Show first few chunks
        print("\nFirst 3 chunks preview:")
        for i, doc in enumerate(documents[:3]):
            print(f"\nChunk {i+1}:")
            print(f"Length: {len(doc.page_content)} characters")
            print(f"Preview: {doc.page_content[:200]}...")
    else:
        print(f"PDF file not found: {pdf_path}")

if __name__ == "__main__":
    main()

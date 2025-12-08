export const metadata = { title: "Real Time Conversation Intelligence" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body 
        style={{ 
          margin: 0, 
          fontFamily: 'Inter, system-ui, Arial, sans-serif', 
          background: 'linear-gradient(135deg,#d299c2,#330867)', // Dynamic & Insightful Gradient
          color: '#f1f5f9', // Light text for readability
          minHeight: '100vh' // Ensures gradient covers the full page
        }}
      >
        {children}
      </body>
    </html>
  );
}



